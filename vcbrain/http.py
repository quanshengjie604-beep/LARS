"""Pooled asynchronous HTTP with atomic disk caching and bounded retries."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

import httpx

from .config import Settings


class Fetcher(Protocol):
    async def get_text(
        self, url: str, *, params: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None
    ) -> str: ...

    async def get_json(
        self, url: str, *, params: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None
    ) -> Any: ...

    async def post_json(
        self, url: str, payload: Mapping[str, Any], *, headers: Mapping[str, str] | None = None
    ) -> Any: ...


class HttpFailure(RuntimeError):
    def __init__(self, url: str, status: int | None, message: str):
        super().__init__(f"{url}: {status or 'network'} {message}")
        self.url = url
        self.status = status


@dataclass(slots=True)
class HttpStats:
    requests: int = 0
    cache_hits: int = 0
    retries: int = 0


class AsyncHttpClient:
    """One connection pool, global/source-level concurrency, and URL single-flight."""

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.stats = HttpStats()
        self._global = asyncio.Semaphore(settings.concurrency)
        self._hosts: dict[str, asyncio.Semaphore] = {}
        self._host_guard = asyncio.Lock()
        self._flights: dict[str, asyncio.Task[bytes]] = {}
        self._flight_guard = asyncio.Lock()
        limits = httpx.Limits(
            max_connections=settings.concurrency,
            max_keepalive_connections=max(4, settings.concurrency // 2),
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_seconds),
            follow_redirects=True,
            limits=limits,
            transport=transport,
            headers={"User-Agent": settings.user_agent, "Accept-Encoding": "gzip, deflate"},
        )

    async def __aenter__(self) -> "AsyncHttpClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _host_limit(self, host: str) -> asyncio.Semaphore:
        async with self._host_guard:
            return self._hosts.setdefault(host, asyncio.Semaphore(self.settings.per_host_concurrency))

    def _cache_key(self, method: str, url: str, payload: Mapping[str, Any] | None) -> str:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")) if payload else ""
        return hashlib.sha256(f"{method}\x1f{url}\x1f{body}".encode()).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.settings.cache_dir / key[:2] / f"{key}.json.gz"

    def _read_cache(self, key: str) -> bytes | None:
        path = self._cache_path(key)
        try:
            if self.settings.cache_ttl_seconds >= 0:
                age = time.time() - path.stat().st_mtime
                if age > self.settings.cache_ttl_seconds:
                    return None
            with gzip.open(path, "rb") as handle:
                record = json.load(handle)
            self.stats.cache_hits += 1
            return record["body"].encode("utf-8")
        except (FileNotFoundError, OSError, KeyError, json.JSONDecodeError):
            return None

    def _write_cache(self, key: str, url: str, body: bytes) -> None:
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        record = {"url": url, "body": body.decode("utf-8", "replace"), "stored_at": time.time()}
        try:
            with gzip.open(tmp, "wt", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    async def _request_bytes(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        cache: bool = True,
    ) -> bytes:
        request = self._client.build_request(method, url, params=params, json=payload, headers=headers)
        cache_key = self._cache_key(method, str(request.url), payload)
        if cache and method == "GET":
            cached = await asyncio.to_thread(self._read_cache, cache_key)
            if cached is not None:
                return cached

        flight_key = cache_key
        async with self._flight_guard:
            task = self._flights.get(flight_key)
            if task is None:
                task = asyncio.create_task(
                    self._uncached_request(request, cache_key=cache_key if cache and method == "GET" else None)
                )
                self._flights[flight_key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                async with self._flight_guard:
                    if self._flights.get(flight_key) is task:
                        self._flights.pop(flight_key, None)

    async def _uncached_request(self, request: httpx.Request, *, cache_key: str | None) -> bytes:
        host_limit = await self._host_limit(request.url.host)
        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                async with self._global, host_limit:
                    self.stats.requests += 1
                    response = await self._client.send(request)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.settings.max_retries:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.35 * (2**attempt)
                    self.stats.retries += 1
                    await asyncio.sleep(min(delay + random.random() * 0.15, 8.0))
                    continue
                response.raise_for_status()
                body = response.content
                if cache_key is not None:
                    await asyncio.to_thread(self._write_cache, cache_key, str(request.url), body)
                return body
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                retryable = status in {429, 500, 502, 503, 504} or status is None
                if not retryable or attempt >= self.settings.max_retries:
                    break
                self.stats.retries += 1
                await asyncio.sleep(min(0.35 * (2**attempt) + random.random() * 0.15, 8.0))
        status = last_error.response.status_code if isinstance(last_error, httpx.HTTPStatusError) else None
        raise HttpFailure(str(request.url), status, str(last_error or "request failed")) from last_error

    async def get_text(
        self, url: str, *, params: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None
    ) -> str:
        return (await self._request_bytes("GET", url, params=params, headers=headers)).decode("utf-8", "replace")

    async def get_json(
        self, url: str, *, params: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None
    ) -> Any:
        raw = await self._request_bytes("GET", url, params=params, headers=headers)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HttpFailure(url, None, "response was not valid JSON") from exc

    async def post_json(
        self, url: str, payload: Mapping[str, Any], *, headers: Mapping[str, str] | None = None
    ) -> Any:
        raw = await self._request_bytes("POST", url, payload=payload, headers=headers, cache=False)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HttpFailure(url, None, "response was not valid JSON") from exc

