"""Tiered anti-bot fetch chain for ``web_fetch`` (v2, 2026-09-24).

Plain ``aiohttp`` gets fingerprinted as a bot at three layers: TLS/JA3
handshake (Python OpenSSL vs browser), HTTP fingerprint (HTTP/1.1 only,
incomplete header set, no cookies) and behaviour (no referer chain). Anti-bot
vendors (Cloudflare, Akamai, ...) block at handshake time, so header/UA
rotation alone cannot fix 403s, and JS challenge pages yield no extractable
text for pure HTTP clients.

The chain therefore degrades through three tiers, confirmed with the user:

  * **Tier 0 ``direct``** — current aiohttp GET. Cheap; wins on sites without
    anti-bot protection.
  * **Tier 1 ``impersonate``** — ``curl_cffi`` (curl-impersonate bindings)
    spoofing Chrome's TLS/JA3/HTTP2 fingerprint, plus cookie warm-up on the
    site homepage and a Referer chain. Optional dependency: when curl_cffi is
    not installed the tier is skipped gracefully (never fatal).
  * **Tier 2 ``reader``** — Jina Reader relay (``https://r.jina.ai/<url>``),
    key-free anonymous (~20 RPM). Server-side rendering defeats JS challenges
    and also converts PDFs to markdown.

The chain advances on: transport error, block status (401/403/429/503),
anti-bot challenge page, empty extraction, or unsupported content type (PDF
is delegated to the reader tier, which can render it).

Behavioural politeness: per-host minimum interval between chain starts and a
single jittered backoff retry on block statuses — hammering a 403 only makes
the block stickier.
"""

import asyncio
import ipaddress
import logging
import os
import random
import re
import socket
import time
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiohttp
from aiohttp.abc import AbstractResolver

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FETCH_TIMEOUT = 20.0
READER_TIMEOUT = 35.0  # relay renders server-side; allow more headroom
DNS_TIMEOUT = 5.0
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
WARMUP_MAX_BYTES = 256 * 1024  # the warm-up only needs the Set-Cookie headers
MAX_REDIRECTS = 5
HOST_MIN_INTERVAL = 1.0  # seconds between chain starts per target host
RETRY_DELAY = (0.8, 1.6)  # jittered backoff for the single retry
BLOCK_STATUSES = frozenset({401, 403, 429, 503})
CHALLENGE_TEXT_CAP = 400  # marker + tiny extracted text => challenge page

# Reader relay endpoint. Override via env for proxied / self-hosted relays
# (r.jina.ai is unreachable from some networks, e.g. mainland China without
# a proxy). Trailing slash optional.
READER_PREFIX = os.environ.get("BOARAI_WEB_READER", "https://r.jina.ai/")
READER_CONNECT_TIMEOUT = 10.0  # fail fast when the relay host is unreachable
READER_FAIL_THRESHOLD = 2  # consecutive transport failures => open circuit
READER_CIRCUIT_SECONDS = 600  # skip the relay while the circuit is open

_reader_fail_count = 0
_reader_unavailable_until = 0.0

# Outbound proxy. ``BOARAI_WEB_PROXY`` (e.g. http://127.0.0.1:7890) takes
# precedence; otherwise standard HTTP(S)_PROXY/ALL_PROXY env vars are honored
# via ``trust_env=True`` on every session below. When a proxy is active the
# proxy resolves target hostnames, so local DNS validation is relaxed to
# "block non-public IP literals" (poisoned DNS answers no longer matter).
WEB_PROXY = os.environ.get("BOARAI_WEB_PROXY", "").strip() or None


def _env_proxy_present() -> bool:
    return any(
        os.environ.get(name, "").strip()
        for name in (
            "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
            "ALL_PROXY", "all_proxy",
        )
    )


PROXY_ACTIVE = WEB_PROXY is not None or _env_proxy_present()

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 "
    "Firefox/126.0",
]

# Substrings typical of interstitial anti-bot pages. Only consulted when the
# extracted text is tiny, so a legitimate article mentioning "captcha" in
# passing never trips the detector.
_CHALLENGE_MARKERS = (
    "cf-browser-verification",
    "challenge-platform",
    "cf_chl",
    "just a moment",
    "checking your browser",
    "check your browser",
    "verify you are human",
    "are you a robot",
    "attention required",
    "access denied",
    "unusual traffic",
    "enable javascript and cookies to continue",
    "captcha",
)

# Optional Tier 1 client. Missing install => tier skipped, never fatal.
try:  # pragma: no cover - import side effect only
    from curl_cffi.requests import AsyncSession as _CCSession  # type: ignore
    from curl_cffi import CurlOpt as _CurlOpt  # type: ignore
    from curl_cffi.curl import (  # type: ignore
        CURL_WRITEFUNC_ERROR as _CC_WRITE_ERROR,
    )
except Exception:  # pragma: no cover
    _CCSession = None
    _CurlOpt = None
    _CC_WRITE_ERROR = 0xFFFFFFFF  # libcurl's CURL_WRITEFUNC_ERROR sentinel


def _detect_cc_curl_options_mode() -> Optional[str]:
    """Which entry point the installed curl_cffi exposes for raw CurlOpt."""
    if _CCSession is None:
        return None
    import inspect

    try:
        if "curl_options" in inspect.signature(_CCSession.request).parameters:
            return "request"
    except (TypeError, ValueError):  # pragma: no cover - exotic callable
        pass
    try:
        from curl_cffi.requests.session import BaseSession  # type: ignore

        if "curl_options" in inspect.signature(BaseSession.__init__).parameters:
            return "session"
    except Exception:  # pragma: no cover - unknown layout => no pinning path
        pass
    return None


_CC_CURL_OPTIONS_MODE = _detect_cc_curl_options_mode()


class UnsafeFetchTarget(ValueError):
    """Raised when a URL resolves to a non-public or invalid destination."""


async def _public_addresses(
    host: str,
    port: int,
    *,
    allow_proxy_dns: bool = False,
) -> List[Dict[str, object]]:
    """Resolve a host once and return only globally routable addresses."""
    loop = asyncio.get_running_loop()
    literal: Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]
    try:
        literal = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None:
        # IP literals are never excused — a literal IS the target.
        if not literal.is_global:
            raise UnsafeFetchTarget(
                f"Non-public network targets are blocked: {host}")
        raw = [(literal.compressed, socket.AF_INET6 if literal.version == 6 else socket.AF_INET)]
    else:
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(
                    host, port, family=socket.AF_UNSPEC,
                    type=socket.SOCK_STREAM,
                ),
                timeout=DNS_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            if allow_proxy_dns:
                logger.debug("Local DNS failed for %s; deferring to proxy", host)
                return []
            raise UnsafeFetchTarget(f"Could not resolve public host: {host}") from exc
        raw = [(item[4][0], item[0]) for item in infos]

    addresses: Dict[tuple[str, int], None] = {}
    for address, family in raw:
        try:
            parsed = ipaddress.ip_address(str(address).split("%", 1)[0])
        except ValueError as exc:
            raise UnsafeFetchTarget(f"Invalid resolved address for host: {host}") from exc
        if not parsed.is_global:
            logger.debug("Filtered non-public DNS answer %s for %s", address, host)
            continue
        addresses[(parsed.compressed, family)] = None
    if not addresses:
        if allow_proxy_dns:
            logger.debug("No public local answer for %s; deferring to proxy", host)
            return []
        raise UnsafeFetchTarget(f"Host has no public address: {host}")
    return [
        {
            "hostname": host,
            "host": address,
            "port": port,
            "family": family,
            "proto": 0,
            "flags": 0,
        }
        for address, family in addresses
    ]


async def validate_fetch_url(url: str) -> List[Dict[str, object]]:
    """Validate an HTTP URL and resolve it to pinned public addresses."""
    parsed = urlparse(str(url))
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise UnsafeFetchTarget("Only absolute http(s) URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeFetchTarget("URLs containing user credentials are blocked")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeFetchTarget("Invalid URL port") from exc
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    if not 1 <= port <= 65535:
        raise UnsafeFetchTarget("URL port must be between 1 and 65535")
    return await _public_addresses(
        parsed.hostname, port, allow_proxy_dns=PROXY_ACTIVE)


class _PublicResolver(AbstractResolver):
    """Resolver that validates and returns the exact addresses it resolved."""

    async def resolve(self, host, port=0, family=socket.AF_INET):
        return await _public_addresses(host, port)

    async def close(self):
        return None


def _curl_resolve_entries(url: str, addresses: List[Dict[str, object]]) -> List[str]:
    parsed = urlparse(url)
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    entries = []
    for item in addresses:
        address = str(item["host"])
        try:
            if ipaddress.ip_address(address).version == 6:
                address = f"[{address}]"
        except ValueError:
            pass
        entries.append(f"{parsed.hostname}:{port}:{address}")
    return entries


# ---------------------------------------------------------------------------
# HTTP helpers (shared with the search side in tools.py)
# ---------------------------------------------------------------------------

def _decode_body(body: bytes, content_type: str) -> str:
    """Decode response bytes honoring the declared charset when possible."""
    charset_match = re.search(r"charset=([\w-]+)", content_type or "")
    if charset_match:
        charset = charset_match.group(1)
        try:
            return body.decode(charset, errors="replace")
        except LookupError:
            pass
    return body.decode("utf-8", errors="replace")


async def _request(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: Dict[str, str],
    timeout: float,
    max_redirects: int = MAX_REDIRECTS,
    connect_timeout: Optional[float] = None,
) -> Tuple[Optional[int], str, Optional[str], str]:
    """GET with manual redirect following."""
    current = url
    for _ in range(max_redirects + 1):
        try:
            await validate_fetch_url(current)
            async with session.get(
                current,
                headers=headers,
                timeout=aiohttp.ClientTimeout(
                    total=timeout, connect=connect_timeout),
                allow_redirects=False,
                proxy=WEB_PROXY,
            ) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        return resp.status, content_type, None, current
                    current = urljoin(str(resp.url), location)
                    continue
                body = await resp.content.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    logger.warning(
                        "Response exceeded %s bytes for %s",
                        MAX_RESPONSE_BYTES,
                        current,
                    )
                    return resp.status, content_type, None, current
                return resp.status, content_type, _decode_body(body, content_type), current
        except UnsafeFetchTarget:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            logger.debug("Request failed for %s: %s", current, exc)
            return None, "", None, current
    return None, "", None, current


# ---------------------------------------------------------------------------
# HTML text extraction (moved from tools.py)
# ---------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    """Strip markup and chrome; keep title + meta description + body text."""

    SKIP_TAGS = frozenset({
        "script", "style", "noscript", "svg", "head", "nav", "header",
        "footer", "aside", "iframe", "form", "button", "select", "textarea",
    })
    BLOCK_TAGS = frozenset({
        "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr",
        "td", "th", "table", "ul", "ol", "section", "article", "blockquote",
        "pre", "hr",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []
        self._skip_depth = 0
        self._in_title = False
        self.title = ""
        self.description = ""

    def _handle_meta(self, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if self.description:
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        name = attr_map.get("name", "").lower() or \
            attr_map.get("property", "").lower()
        if name in ("description", "og:description", "twitter:description"):
            self.description = attr_map.get("content", "").strip()

    def handle_starttag(self, tag: str,
                        attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            self._handle_meta(attrs)
        if tag in self.BLOCK_TAGS and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str,
                           attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag == "meta":
            self._handle_meta(attrs)
        if tag in self.BLOCK_TAGS and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK_TAGS and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.title:
            self.title = data.strip()
        if self._skip_depth == 0:
            self._parts.append(data)


def _extract_page(html_text: str) -> Tuple[str, str, str]:
    """Return (title, description, cleaned_body_text)."""
    extractor = _TextExtractor()
    try:
        extractor.feed(html_text)
    except Exception:
        logger.debug("HTMLParser error while extracting page", exc_info=True)
    text = "".join(extractor._parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return extractor.title, extractor.description, text.strip()


# ---------------------------------------------------------------------------
# Tier plumbing
# ---------------------------------------------------------------------------

class TierResult:
    """Outcome of one fetch tier."""

    __slots__ = ("ok", "title", "text", "error", "skipped", "terminal")

    def __init__(self, ok: bool, title: Optional[str] = None,
                 text: Optional[str] = None,
                 error: Optional[Dict[str, str]] = None,
                 skipped: bool = False,
                 terminal: bool = False) -> None:
        self.ok = ok
        self.title = title
        self.text = text
        self.error = error
        self.skipped = skipped
        # Deterministic 4xx (404/410): when two HTTP tiers agree the resource
        # simply does not exist, the chain stops instead of burning the relay.
        self.terminal = terminal


def _looks_like_challenge(html_text: str, extracted_len: int) -> bool:
    """True when the page is an anti-bot interstitial."""
    if extracted_len >= CHALLENGE_TEXT_CAP:
        return False
    low = html_text[:100_000].lower()
    return any(marker in low for marker in _CHALLENGE_MARKERS)


def _evaluate_response(url: str, status: Optional[int], content_type: str,
                       body: Optional[str]) -> TierResult:
    """Shared verdict for HTTP-style tiers (direct + impersonate)."""
    if status is None:
        return TierResult(False, error={
            "error": "Failed to fetch {} (network error or timeout)".format(url)})
    if status in BLOCK_STATUSES:
        return TierResult(False, error={
            "error": "Failed to fetch {}: HTTP {} (anti-bot block)".format(url, status)})
    if status != 200:
        return TierResult(False, terminal=status in (404, 410), error={
            "error": "Failed to fetch {}: HTTP {}".format(url, status)})
    ctype = (content_type or "").lower()
    if "pdf" in ctype:
        # Delegate: the reader tier renders PDFs to markdown.
        return TierResult(False, error={
            "error": "{} is a PDF; delegating to reader tier".format(url)})
    if body is None:
        return TierResult(False, error={
            "error": "Response body exceeds the 10 MiB fetch limit for {}".format(url)})
    if not body:
        return TierResult(False, error={"error": "Empty body from {}".format(url)})
    if "\x00" in body[:4096]:
        return TierResult(False, error={
            "error": "{} appears to be a binary file; cannot extract text".format(url)})
    if "html" in ctype:
        title, description, text = _extract_page(body)
        if not text:
            return TierResult(False, error={
                "error": "No extractable text from {}".format(url)})
        if _looks_like_challenge(body, len(text)):
            return TierResult(False, error={
                "error": "{} served an anti-bot challenge page".format(url)})
        if not title and description:
            title = description
        return TierResult(True, title=title, text=text)
    # JSON / plain text / XML: keep raw text as-is
    text = body.strip()
    if not text:
        return TierResult(False, error={"error": "Empty content from {}".format(url)})
    return TierResult(True, title=url, text=text)


def _fetch_headers() -> Dict[str, str]:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


async def _tier_direct(url: str) -> TierResult:
    """Tier 0: plain aiohttp GET with one jittered backoff retry on blocks."""
    last = TierResult(False, error={"error": "not attempted"})
    connector = aiohttp.TCPConnector(
        resolver=_PublicResolver(), use_dns_cache=False
    )
    async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
        for attempt in (0, 1):
            try:
                status, content_type, body, final_url = await _request(
                    session, url, headers=_fetch_headers(), timeout=FETCH_TIMEOUT,
                    max_redirects=MAX_REDIRECTS)
            except UnsafeFetchTarget as exc:
                return TierResult(False, terminal=True, error={
                    "error": str(exc), "blocked": "true"})
            last = _evaluate_response(url, status, content_type, body)
            if final_url != url and last.ok:
                last.title = last.title or final_url
            if last.ok or status is None or status not in BLOCK_STATUSES:
                return last
            if attempt == 0:
                await asyncio.sleep(random.uniform(*RETRY_DELAY))
    return last


class _BodySink:
    """curl_cffi write callback that accumulates a body up to a byte cap."""

    __slots__ = ("limit", "parts", "size", "overflow")

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.parts: List[bytes] = []
        self.size = 0
        self.overflow = False

    def __call__(self, chunk: bytes) -> int:
        self.size += len(chunk)
        if self.size > self.limit:
            # Returning anything but ``len(chunk)`` makes libcurl abort the
            # transfer with CURLE_WRITE_ERROR — that is how the byte cap stops
            # the download instead of merely truncating it in memory.
            self.overflow = True
            return _CC_WRITE_ERROR
        self.parts.append(chunk)
        return len(chunk)

    def text(self, content_type: str) -> Optional[str]:
        """Decoded body, or None when the cap aborted the transfer."""
        if self.overflow:
            return None
        return _decode_body(b"".join(self.parts), content_type)


async def _tier_impersonate(url: str) -> TierResult:
    """Tier 1: curl_cffi browser impersonation (TLS/JA3/HTTP2) + cookies."""
    # Non-stream requests ONLY. ``stream=True`` makes curl_cffi 0.15 hand the
    # curl handle to a background task whose done-callback releases it, and on
    # the header-error path ``_request_once`` releases that same handle a
    # second time: the pool ends up holding one handle twice, the late callback
    # removes the NEXT request's handle from curl_multi (killing an unrelated
    # in-flight fetch with CancelledError), and stopping a stream early needs
    # the sync ``Response.close()``, which frees the handle underneath that
    # callback — logging "TypeError: initializer for ctype 'void *' must be a
    # cdata pointer, not NoneType" both as an asyncio callback error and out of
    # ``AsyncSession.close()``. ``content_callback`` enforces the same byte cap
    # with a single synchronous release inside ``_request_once``.
    if _CCSession is None or _CurlOpt is None or _CC_CURL_OPTIONS_MODE is None:
        return TierResult(False, skipped=True, error={
            "error": "impersonate tier skipped (safe DNS pinning unavailable)"})
    parsed = urlparse(url)
    home = "{}://{}/".format(parsed.scheme, parsed.netloc)
    last = TierResult(False, error={"error": "not attempted"})

    async def pinned_get(session, target: str, headers: Dict[str, str],
                         timeout: float, limit: int = MAX_RESPONSE_BYTES):
        """GET with per-hop validation, manual redirects and a response byte cap."""
        current = target
        for redirect_index in range(MAX_REDIRECTS + 1):
            addresses = await validate_fetch_url(current)
            # IP pinning is impossible through a proxy (the proxy resolves
            # upstream); RESOLVE entries would pin the proxy's connection to
            # stale/poisoned local answers.
            curl_options = None
            if addresses and not PROXY_ACTIVE:
                curl_options = {
                    _CurlOpt.RESOLVE: _curl_resolve_entries(current, addresses)
                }
            extra_kwargs: Dict[str, object] = {}
            if _CC_CURL_OPTIONS_MODE == "request":
                extra_kwargs["curl_options"] = curl_options
            else:
                # curl_cffi >= 0.15: session-level dict, re-read at every
                # request. Reassign per hop so each redirect target stays
                # pinned to its own validated addresses and stale pinning
                # never leaks into the next request.
                session.curl_options = curl_options or {}
            sink = _BodySink(limit)
            try:
                response = await session.get(
                    current,
                    timeout=timeout,
                    headers=headers,
                    allow_redirects=False,
                    content_callback=sink,
                    **extra_kwargs,
                )
            except Exception as exc:
                # Hitting the cap aborts the transfer, so the body surfaces as
                # an error; the parsed status/headers ride along on it.
                if not sink.overflow:
                    raise
                response = getattr(exc, "response", None)
                if response is None:
                    raise
            if response.status_code not in (301, 302, 303, 307, 308):
                return response, current, sink
            location = response.headers.get("Location")
            if not location:
                return response, current, sink
            if redirect_index >= MAX_REDIRECTS:
                raise UnsafeFetchTarget("Too many redirects")
            current = urljoin(current, location)
        raise UnsafeFetchTarget("Too many redirects")

    # One session for warm-up + both attempts: the retry must keep the
    # homepage cookies, otherwise it degrades back to a cold request.
    # Not ``async with``: a curl_cffi teardown hiccup must never fail the tool.
    session = _CCSession(
        impersonate="chrome", proxy=WEB_PROXY, trust_env=True
    )
    try:
        # Cookie warm-up + referer chain: browsers rarely deep-link cold,
        # and session cookies defuse lightweight bot checks. Only the response
        # headers matter here, so the homepage body is capped hard.
        try:
            await pinned_get(
                session, home, {"Accept": "text/html,*/*;q=0.8"}, 10,
                limit=WARMUP_MAX_BYTES,
            )
        except UnsafeFetchTarget as exc:
            return TierResult(False, terminal=True, error={
                "error": str(exc), "blocked": "true"})
        except Exception:
            logger.debug("Homepage warm-up failed for %s", home)
        for attempt in (0, 1):
            try:
                resp, final_url, sink = await pinned_get(
                    session,
                    url,
                    {"Referer": home,
                     "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
                    FETCH_TIMEOUT,
                )
            except UnsafeFetchTarget as exc:
                return TierResult(False, terminal=True, error={
                    "error": str(exc), "blocked": "true"})
            except Exception as exc:
                logger.debug("Impersonate fetch failed for %s: %s", url, exc)
                last = TierResult(False, error={
                    "error": "Impersonate fetch failed for {}: {}".format(url, exc)})
                if attempt == 0:
                    await asyncio.sleep(random.uniform(*RETRY_DELAY))
                continue
            status = resp.status_code
            try:
                content_type = resp.headers.get("Content-Type", "") or ""
            except Exception:
                content_type = ""
            last = _evaluate_response(
                final_url, status, content_type, sink.text(content_type))
            if last.ok or status not in BLOCK_STATUSES:
                return last
            if attempt == 0:
                await asyncio.sleep(random.uniform(*RETRY_DELAY))
    finally:
        try:
            await session.close()
        except Exception as exc:  # noqa: BLE001 - teardown only
            logger.debug("curl_cffi session close failed: %s", exc)
    return last


def _parse_reader(body: str) -> Tuple[str, str]:
    """Split Jina Reader output into (title, markdown body)."""
    title = ""
    match = re.search(r"(?mi)^Title:\s*(.+)$", body)
    if match:
        title = match.group(1).strip()
    text = body
    idx = body.find("Markdown Content:")
    if idx >= 0:
        text = body[idx + len("Markdown Content:"):]
    return title, text.strip()


async def _tier_reader(url: str) -> TierResult:
    """Tier 2: key-free Jina Reader relay (server-side render, ~20 RPM)."""
    global _reader_fail_count, _reader_unavailable_until
    if os.getenv("BOARAI_ALLOW_WEB_READER_RELAY", "").strip() != "1":
        return TierResult(False, skipped=True, error={
            "error": "reader relay disabled; set BOARAI_ALLOW_WEB_READER_RELAY=1 to share URLs with the relay"})
    if time.monotonic() < _reader_unavailable_until:
        return TierResult(False, skipped=True, error={
            "error": "reader relay circuit open (unreachable from this network)"})
    prefix = READER_PREFIX if READER_PREFIX.endswith("/") else READER_PREFIX + "/"
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/plain,text/markdown;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    connector = aiohttp.TCPConnector(
        resolver=_PublicResolver(), use_dns_cache=False
    )
    try:
        async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
            status, _, body, _ = await _request(
                session, prefix + url, headers=headers,
                timeout=READER_TIMEOUT, connect_timeout=READER_CONNECT_TIMEOUT,
                max_redirects=2)
    except UnsafeFetchTarget as exc:
        return TierResult(False, terminal=True, error={
            "error": str(exc), "blocked": "true"})
    if status is None:
        _reader_fail_count += 1
        if _reader_fail_count >= READER_FAIL_THRESHOLD:
            _reader_unavailable_until = time.monotonic() + READER_CIRCUIT_SECONDS
            logger.info(
                "Reader relay circuit opened for %s s after %d transport "
                "failures", READER_CIRCUIT_SECONDS, _reader_fail_count)
        return TierResult(False, error={
            "error": "Reader relay unreachable for {}".format(url)})
    _reader_fail_count = 0  # any HTTP response proves the relay reachable
    if status == 429:
        return TierResult(False, error={
            "error": "Reader relay rate-limited (key-free ~20 RPM) for {}".format(url)})
    if status != 200 or not body:
        if status == 200 and body is None:
            return TierResult(False, error={
                "error": "Reader response exceeds the 10 MiB fetch limit for {}".format(url)})
        return TierResult(False, error={
            "error": "Reader relay failed for {}: HTTP {}".format(url, status)})
    title, text = _parse_reader(body)
    if not text:
        return TierResult(False, error={
            "error": "Reader relay returned no content for {}".format(url)})
    return TierResult(True, title=title or url, text=text)


# ---------------------------------------------------------------------------
# Per-host politeness throttle
# ---------------------------------------------------------------------------

_host_last: Dict[str, float] = {}
_host_lock = asyncio.Lock()


async def _throttle(host: str) -> None:
    """Enforce HOST_MIN_INTERVAL between chain starts per target host."""
    if not host:
        return
    wait = 0.0
    async with _host_lock:
        now = time.monotonic()
        due = _host_last.get(host, 0.0) + HOST_MIN_INTERVAL
        if now < due:
            wait = due - now
            _host_last[host] = due
        else:
            _host_last[host] = now
    if wait > 0:
        await asyncio.sleep(wait)


# ---------------------------------------------------------------------------
# Chain entry point
# ---------------------------------------------------------------------------

def _final_error(url: str,
                 trail: List[Tuple[str, Dict[str, str], bool]]) -> Dict[str, str]:
    """Aggregate per-tier failures into one actionable error dict."""
    real = ["[{}] {}".format(tier, err.get("error", "unknown"))
            for tier, err, skipped in trail if not skipped]
    skipped_tiers = [tier for tier, _, skipped in trail if skipped]
    detail = "; ".join(real) if real else "all tiers failed"
    out: Dict[str, str] = {
        "error": "Failed to fetch {} after tiers [direct, impersonate, "
                 "reader]: {}".format(url, detail)}
    hints = []
    if "impersonate" in skipped_tiers:
        hints.append("pip install curl_cffi enables TLS-fingerprint "
                     "impersonation")
    reader_errors = [
        err.get("error", "")
        for tier, err, skipped in trail
        if tier == "reader" and skipped
    ]
    if any("disabled" in error for error in reader_errors):
        hints.append(
            "reader relay is disabled; set BOARAI_ALLOW_WEB_READER_RELAY=1 "
            "to share URLs with it"
        )
    elif reader_errors:
        hints.append(
            "reader relay circuit is open; set BOARAI_WEB_READER to a "
            "reachable public relay"
        )
    if hints:
        out["note"] = "; ".join(hints)
    return out


async def fetch_url_chain(
    url: str,
) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, str]], str]:
    """Run the tier chain. Returns ``(title, text, error, tier)``."""
    try:
        await validate_fetch_url(url)
    except UnsafeFetchTarget as exc:
        return None, None, {"error": str(exc)}, "none"
    host = urlparse(url).hostname or ""
    await _throttle(host)
    trail: List[Tuple[str, Dict[str, str], bool]] = []
    terminal_before = False
    for tier_name, tier_fn in (
        ("direct", _tier_direct),
        ("impersonate", _tier_impersonate),
        ("reader", _tier_reader),
    ):
        result = await tier_fn(url)
        if result.ok:
            return result.title, result.text, None, tier_name
        if result.error and result.error.get("blocked") == "true":
            return None, None, {"error": result.error.get("error", "Unsafe redirect blocked")}, "none"
        trail.append((tier_name, result.error or {"error": "unknown"},
                      result.skipped))
        # Two consecutive deterministic 4xx from both HTTP tiers => the
        # resource really does not exist; don't waste the relay hop.
        if result.terminal and tier_name == "impersonate" and terminal_before:
            break
        terminal_before = result.terminal
    return None, None, _final_error(url, trail), "none"
