"""Thread-safe HTTP client: disk cache + per-host rate limiting + backoff.

Stdlib-only (urllib). Every successful GET is cached to a content-addressed file
so reruns are free and the raw payloads double as evidence (plan §2.2).
"""

from __future__ import annotations

import gzip
import io
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from . import config
from .util import stable_hash


class RateLimiter:
    """Simple per-host minimum-interval throttle, shared across threads."""

    def __init__(self, limits: dict):
        self._min_interval = {h: (1.0 / r if r > 0 else 0.0) for h, r in limits.items()}
        self._default = 1.0 / limits.get("_default", 3.0)
        self._last: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, host: str) -> threading.Lock:
        with self._guard:
            if host not in self._locks:
                self._locks[host] = threading.Lock()
            return self._locks[host]

    def wait(self, host: str) -> None:
        interval = self._min_interval.get(host, self._default)
        if interval <= 0:
            return
        lock = self._lock_for(host)
        with lock:
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            delay = interval - (now - last)
            if delay > 0:
                time.sleep(delay)
            self._last[host] = time.monotonic()


_LIMITER = RateLimiter(config.RATE_LIMITS)


def _cache_path(key: str) -> str:
    sub = key[:2]
    d = os.path.join(config.CACHE_DIR, sub)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, key + ".json")


def _cache_read(key: str) -> Optional[dict]:
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    if config.CACHE_TTL_SECONDS > 0 and (time.time() - os.path.getmtime(path)) > config.CACHE_TTL_SECONDS:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _cache_write(key: str, record: dict) -> None:
    try:
        with open(_cache_path(key), "w", encoding="utf-8") as f:
            json.dump(record, f)
    except OSError:
        pass


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.url = url
        self.body = body


def _do_request(url: str, headers: dict, data: Optional[bytes], method: str) -> tuple[int, str]:
    host = urllib.parse.urlparse(url).netloc
    _LIMITER.wait(host)
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", config.USER_AGENT)
    req.add_header("Accept-Encoding", "gzip")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT, context=config.ssl_context()) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return resp.status, raw.decode("utf-8", errors="replace")


def request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    use_cache: bool = True,
) -> tuple[int, str]:
    """Return (status, text). Caches successful GETs. Retries with backoff on
    429/5xx and honours a numeric Retry-After when present."""
    headers = dict(headers or {})
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
        method = "POST"

    cacheable = use_cache and method == "GET"
    key = stable_hash(method, url, json.dumps(json_body, sort_keys=True) if json_body else "")
    if cacheable:
        hit = _cache_read(key)
        if hit is not None:
            return hit["status"], hit["text"]

    last_exc: Optional[Exception] = None
    for attempt in range(config.HTTP_RETRIES):
        try:
            status, text = _do_request(url, headers, data, method)
            if cacheable and 200 <= status < 300:
                _cache_write(key, {"status": status, "text": text, "url": url})
            return status, text
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if e.code in (429, 500, 502, 503, 504) and attempt < config.HTTP_RETRIES - 1:
                retry_after = e.headers.get("Retry-After") if e.headers else None
                wait = float(retry_after) if (retry_after or "").isdigit() else (2 ** attempt) + 0.5
                time.sleep(min(wait, 30))
                last_exc = e
                continue
            # 403 from GitHub is usually rate-limit; surface as HttpError to let caller degrade
            raise HttpError(e.code, url, body) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_exc = e
            if attempt < config.HTTP_RETRIES - 1:
                time.sleep((2 ** attempt) + 0.5)
                continue
            raise HttpError(0, url, str(e)) from e
    if last_exc:
        raise HttpError(0, url, str(last_exc))
    raise HttpError(0, url, "unreachable")


def get_json(url: str, **kw) -> Optional[dict]:
    status, text = request(url, **kw)
    if not (200 <= status < 300):
        raise HttpError(status, url, text[:500])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def post_json(url: str, json_body: dict, headers: Optional[dict] = None, use_cache: bool = True) -> Optional[dict]:
    status, text = request(url, method="POST", json_body=json_body, headers=headers, use_cache=use_cache)
    if not (200 <= status < 300):
        raise HttpError(status, url, text[:500])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
