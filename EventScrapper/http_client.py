import hashlib
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit

import requests
from requests.structures import CaseInsensitiveDict

from EventScrapper.models import IncompleteScan, SourceBlocked, SourceError, canonical_url

USER_AGENT = "Dundaa/2.0 (+https://github.com/darthlotu5/WhatsHappening; public-event-index)"
PERSISTED_HEADERS = ("ETag", "Last-Modified", "Content-Type", "Content-Range",
                     "Link", "X-Total-Count", "X-Total-Pages", "Cache-Control")


class SourceUnavailable(SourceError):
    status = "unavailable"


class SourceRateLimited(SourceError):
    status = "rate_limited"


def robots_policy(text, url, agent="Dundaa"):
    groups, agents, rules, delay = [], [], [], 1.0
    has_directives = False
    for raw in text.splitlines() + ["User-agent: __end__"]:
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if has_directives:
                groups.append((agents, rules, delay))
                agents, rules, delay, has_directives = [], [], 1.0, False
            agents.append(value.lower())
        elif agents and key in ("allow", "disallow", "crawl-delay"):
            has_directives = True
            if key == "crawl-delay":
                try:
                    delay = max(1.0, float(value))
                except ValueError as error:
                    raise SourceError("Invalid robots.txt crawl-delay") from error
            elif value:
                rules.append((key == "allow", value))
    matching = []
    for agents, rules, delay in groups:
        weights = [0 if name == "*" else len(name) for name in agents
                   if name == "*" or name in agent.lower()]
        if weights:
            matching.append((max(weights), rules, delay))
    if not matching:
        return True, 1.0
    best = max(item[0] for item in matching)
    target = urlsplit(url)
    path = unquote(target.path or "/") + ("?" + target.query if target.query else "")
    matches = []
    crawl_delay = 1.0
    for weight, rules, delay in matching:
        if weight != best:
            continue
        crawl_delay = max(crawl_delay, delay)
        for allowed, pattern in rules:
            pattern = unquote(pattern)
            end = pattern.endswith("$")
            value = pattern[:-1] if end else pattern
            expression = "^" + re.escape(value).replace(r"\*", ".*") + ("$" if end else "")
            if re.search(expression, path):
                matches.append((len(value.replace("*", "")), allowed))
    return (max(matches)[1] if matches else True), crawl_delay


@dataclass
class Response:
    url: str
    text: str
    status_code: int
    headers: dict
    from_cache: bool = False

    def json(self):
        return json.loads(self.text)


class CachedClient:
    def __init__(self, source, state, *, session=None, clock=time.time, sleep=time.sleep,
                 max_requests=1000, max_bytes=128 * 1024 * 1024):
        self.source = source
        self.state = state
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.clock, self.sleep = clock, sleep
        self.max_requests, self.max_bytes = max_requests, max_bytes
        self.last_request = {}
        self.stats = {"requests": 0, "bytes": 0, "not_modified": 0,
                      "cache_hits": 0, "unchanged_pages": 0}
        self.pages = state.setdefault("pages", {})
        for url, page in list(self.pages.items()):
            if self.clock() - page.get("last_used_at", page["fetched_at"]) > 30 * 86400:
                del self.pages[url]
        self.robots = state.setdefault("robots", {})

    def _allowed_host(self, url):
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in self.source.hosts or parts.port not in (None, 443):
            raise SourceBlocked(f"URL outside approved HTTPS source hosts: {url}")
        if parts.username or parts.password:
            raise SourceBlocked("Credentials in source URL are not allowed")

    def _request(self, url, headers=None, delay=1.0):
        if self.stats["requests"] >= self.max_requests:
            raise IncompleteScan("Request safety limit reached before pagination completed")
        host = urlsplit(url).netloc
        wait = delay - (self.clock() - self.last_request.get(host, -delay))
        if wait > 0:
            self.sleep(wait)
        self.stats["requests"] += 1
        try:
            response = self.session.get(url, headers=headers or {}, timeout=(10, 30),
                                        allow_redirects=False, stream=True)
            self.last_request[host] = self.clock()
            with response:
                body = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    body.extend(chunk)
                    self.stats["bytes"] += len(chunk)
                    if len(body) > 8 * 1024 * 1024 or self.stats["bytes"] > self.max_bytes:
                        raise IncompleteScan("Response byte safety limit reached")
                encoding = response.encoding if "charset=" in response.headers.get("Content-Type", "") else "utf-8"
                return Response(url, body.decode(encoding or "utf-8", errors="replace"),
                                response.status_code, CaseInsensitiveDict(response.headers))
        except requests.RequestException as error:
            self.last_request[host] = self.clock()
            raise SourceUnavailable(f"Unable to fetch {url}: {error}") from error

    def _check_robots(self, url):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = self.robots.get(origin)
        if not cached or self.clock() - cached["checked_at"] >= 86400:
            target = origin + "/robots.txt"
            for _ in range(6):
                self._allowed_host(target)
                response = self._request(target)
                if response.status_code in (301, 302, 303, 307, 308):
                    target = urljoin(target, response.headers.get("Location", ""))
                    continue
                if response.status_code in (404, 405, 410):
                    text = ""
                elif response.status_code == 200:
                    text = response.text
                elif response.status_code in (401, 403):
                    raise SourceBlocked(f"robots.txt access denied: {target}")
                else:
                    raise SourceUnavailable(f"robots.txt returned HTTP {response.status_code}: {target}")
                cached = {"text": text, "checked_at": self.clock()}
                self.robots[origin] = cached
                break
            else:
                raise SourceError("robots.txt redirect limit exceeded")
        allowed, delay = robots_policy(cached["text"], url)
        if not allowed:
            raise SourceBlocked(f"robots.txt disallows {url}")
        return delay

    def get(self, url, *, detail=False, headers=None):
        url = canonical_url(url, self.source.listing_url)
        request_headers = dict(headers or {})
        original_host = urlsplit(url).hostname
        for _ in range(6):
            self._allowed_host(url)
            if request_headers and urlsplit(url).hostname != original_host:
                raise SourceBlocked("Cross-host redirects with API headers are not allowed")
            delay = self._check_robots(url)
            cached = self.pages.get(url)
            if detail and cached:
                ttl = min(86400, 21600 * 2 ** min(cached.get("unchanged", 0), 2))
                control = cached["headers"].get("Cache-Control", "").lower()
                max_age = re.search(r'(?:^|,)\s*max-age\s*=\s*"?(\d+)', control)
                if max_age:
                    ttl = min(ttl, int(max_age.group(1)))
                if "no-cache" not in control and "max-age=0" not in control and self.clock() - cached["fetched_at"] < ttl:
                    self.stats["cache_hits"] += 1
                    cached["last_used_at"] = self.clock()
                    return Response(url, cached["body"], 200, CaseInsensitiveDict(cached["headers"]), True)
            conditional_headers = dict(request_headers)
            if cached:
                if cached["headers"].get("ETag"):
                    conditional_headers["If-None-Match"] = cached["headers"]["ETag"]
                if cached["headers"].get("Last-Modified"):
                    conditional_headers["If-Modified-Since"] = cached["headers"]["Last-Modified"]
            response = self._request(url, conditional_headers, delay)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise SourceError("Source redirect has no Location header")
                url = canonical_url(location, url)
                continue
            if response.status_code == 304:
                if not cached:
                    raise SourceError("Source returned304 without cached content")
                self.stats["not_modified"] += 1
                cached["fetched_at"] = self.clock()
                cached["last_used_at"] = self.clock()
                cached["unchanged"] = cached.get("unchanged", 0) + 1
                cached["headers"].update({key: response.headers[key] for key in PERSISTED_HEADERS
                                          if key in response.headers})
                if "no-store" in cached["headers"].get("Cache-Control", "").lower():
                    self.pages.pop(url, None)
                return Response(url, cached["body"], 200, CaseInsensitiveDict(cached["headers"]), True)
            if response.status_code in (401, 403):
                raise SourceBlocked(f"HTTP {response.status_code} access denied: {url}")
            if response.status_code == 429:
                raise SourceRateLimited(f"HTTP429 from {url}; Retry-After={response.headers.get('Retry-After', 'unspecified')}")
            if response.status_code != 200:
                raise SourceUnavailable(f"HTTP {response.status_code}: {url}")
            if "no-store" in response.headers.get("Cache-Control", "").lower():
                self.pages.pop(url, None)
                return response
            digest = hashlib.sha256(response.text.encode()).hexdigest()
            unchanged = bool(cached and cached["sha256"] == digest)
            self.stats["unchanged_pages"] += int(unchanged)
            stored_headers = {key: response.headers[key] for key in PERSISTED_HEADERS
                              if key in response.headers}
            self.pages[url] = {"body": response.text, "headers": stored_headers,
                               "sha256": digest, "fetched_at": self.clock(),
                               "last_used_at": self.clock(),
                               "unchanged": cached.get("unchanged", 0) + 1 if unchanged else 0}
            return response
        raise SourceError("Source redirect limit exceeded")
