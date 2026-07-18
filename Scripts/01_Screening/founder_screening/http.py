from __future__ import annotations

import logging
import time
import urllib.robotparser
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests

LOGGER = logging.getLogger(__name__)


@dataclass
class PoliteHttpClient:
    user_agent: str
    timeout_seconds: int = 30
    default_delay_seconds: float = 1.0
    session: requests.Session = field(default_factory=requests.Session)
    _last_request: dict[str, float] = field(default_factory=dict)
    _robots: dict[str, urllib.robotparser.RobotFileParser] = field(default_factory=dict)

    def __post_init__(self):
        self.session.headers.update({"User-Agent": self.user_agent})

    def _wait(self, url: str, minimum_delay: float | None = None):
        host = urlparse(url).netloc
        delay = self.default_delay_seconds if minimum_delay is None else minimum_delay
        remaining = delay - (time.monotonic() - self._last_request.get(host, 0.0))
        if remaining > 0:
            time.sleep(remaining)

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(urljoin(root, "/robots.txt"))
            try:
                parser.read()
            except Exception as exc:
                LOGGER.warning("robots.txt unavailable for %s: %s; skipping for safety", root, exc)
                return False
            self._robots[root] = parser
        return self._robots[root].can_fetch(self.user_agent, url)

    def get(self, url: str, *, params=None, headers=None, minimum_delay=None, check_robots=False) -> requests.Response:
        if check_robots and not self.allowed(url):
            raise PermissionError(f"robots.txt does not allow crawling {url}")
        self._wait(url, minimum_delay)
        response = self.session.get(url, params=params, headers=headers, timeout=self.timeout_seconds)
        self._last_request[urlparse(response.url).netloc] = time.monotonic()
        if response.status_code in (403, 429):
            retry = response.headers.get("Retry-After")
            if retry:
                try:
                    delay = float(retry)
                except ValueError:
                    delay = max(0, (parsedate_to_datetime(retry).timestamp() - time.time()))
                LOGGER.warning("Rate limited by %s; retry-after=%ss", response.url, delay)
            raise requests.HTTPError(f"rate limited: {response.status_code} {response.url}", response=response)
        response.raise_for_status()
        return response

