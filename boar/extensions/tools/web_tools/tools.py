"""Web access tools — key-free ``web_search`` (domestic Chinese engines:
Baidu / 360 / Sogou) and ``web_fetch`` (URL -> clean readable text).

Design decisions:
  * **No API keys.** Search scrapes engines reachable from mainland China.
    Bing serves anti-bot challenge pages and DuckDuckGo is largely unreachable
    (poisoned DNS), so v3 (2026-10-22, confirmed with the user) replaced both
    with Baidu, 360 (``so.com``) and Sogou. Baidu and 360 embed the real target
    URL in the SERP (``mu="..."`` / ``data-mdurl="..."``), so their results
    cost zero extra hops; Sogou wraps every link in a ``/link?url=...``
    JS-redirect page and is therefore only the last fallback, where one tiny
    extra request per result is acceptable. Anti-scraping countermeasures (UA
    rotation, Sec-Fetch headers, no Accept-Encoding, relevance gating,
    per-engine circuit breaker) are adapted from the openclacky project, which
    proved they are required for key-free scraping to stay reliable.
  * **Truncate-and-store without the file_read tool.** ``web_fetch`` keeps the
    full extracted text in ``.boarai/cache/web/`` (JSON, 30 min TTL) and
    returns a window starting at ``offset``. To read on, the model simply
    calls ``web_fetch(url, offset=N)`` again — the cached copy is sliced,
    never refetched. No file-system tools are involved.
  * **SSRF protection**: only globally routable HTTP(S) targets are fetched;
    every redirect is revalidated and DNS results are pinned for direct fetches.
    The external Reader relay is disabled unless explicitly opted in.
  * Pure stdlib + ``aiohttp`` only; async throughout. SERPs are fetched with
    stdlib ``urllib`` inside ``asyncio.to_thread`` — see ``_fetch_serp_sync``
    for why aiohttp is rejected by all three engines.

v2 (2026-09-24, confirmed with the user): ``web_fetch`` gained a tiered
anti-bot fetch chain in ``fetch_chain.py`` — Tier 0 plain aiohttp, Tier 1
optional ``curl_cffi`` TLS/JA3/HTTP2 browser impersonation with cookie
warm-up, Tier 2 key-free Jina Reader relay (``r.jina.ai``) for JS-challenge
pages and PDFs. ``curl_cffi`` is an optional dependency: missing install
degrades gracefully, it is deliberately NOT listed in extension.yaml
``pip_dependencies`` because the loader fails fast on missing declared deps
and that would take the whole extension (incl. web_search) down.
"""

import asyncio
import gzip
import hashlib
import html
import http.cookiejar
import json
import logging
import random
import re
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import (
    HTTPCookieProcessor,
    ProxyHandler,
    Request,
    build_opener,
)

from boar.extensions.tools.web_tools.fetch_chain import (
    _USER_AGENTS,
    WEB_PROXY,
    UnsafeFetchTarget,
    fetch_url_chain,
    validate_fetch_url,
)
from boarai.core.extension.tool import ToolParameter, tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_CHARS = 8000
MAX_CHARS_CAP = 30000
MIN_CHARS = 500
SEARCH_TIMEOUT = 8.0
CACHE_TTL = 30 * 60  # 30 minutes
MAX_STORED_CHARS = 2_000_000  # per-page cap for the on-disk cache
ENGINE_CIRCUIT_SECONDS = 600  # hold an engine open after an irrelevant page

# Every engine the dispatcher knows how to call. The ACTIVE set, its order and
# each SERP URL come from the extension config (`engine_urls`); this tuple is
# only the default and the validation whitelist.
KNOWN_ENGINES = ("baidu", "so360", "sogou", "bing", "google")
DEFAULT_ENGINES_TEXT = "baidu, so360, sogou, bing, google"

# Full SERP link per engine — the settings panel shows ONE input per engine
# with this complete URL. ``{query}`` is the URL-encoded query; ``{count}`` is
# the per-request result-count hint (only Baidu's ``rn`` consumes it). A
# custom URL is still host-validated against the engine's own domain at fetch
# time, so pointing one engine's link at another site cannot bypass the
# allowlist.
DEFAULT_ENGINE_URLS = {
    "baidu": "https://www.baidu.com/s?ie=utf-8&rn={count}&wd={query}",
    "so360": "https://www.so.com/s?ie=utf-8&q={query}",
    "sogou": "https://www.sogou.com/web?ie=utf8&query={query}",
    "bing": "https://www.bing.com/search?q={query}&count={count}",
    "google": "https://www.google.com/search?q={query}&num={count}&hl=zh-CN",
}
# Active entries, in priority order: {name(parser), label, url}. The same
# parser may appear several times with different links/labels.
_ACTIVE_SPECS: List[Dict[str, str]] = [
    {"name": n, "label": n, "url": DEFAULT_ENGINE_URLS[n]} for n in KNOWN_ENGINES
]

_fetch_locks: Dict[str, asyncio.Lock] = {}
_cache_write_counter = 0
_engine_blocked_until: Dict[str, float] = {}


# ---------------------------------------------------------------------------
# Search: domestic engines — Baidu + 360 in parallel, Sogou as fallback
# ---------------------------------------------------------------------------

def _search_headers(language: str, host: str) -> Dict[str, str]:
    """Browser-like headers; the caller supplies Accept-Encoding explicitly (stdlib urllib does not negotiate compression by itself)."""
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": language,
        "Referer": "https://{}/".format(host),
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Connection": "keep-alive",
    }


# Each engine's SERP is fetched with stdlib ``urllib`` in a worker thread
# rather than aiohttp. Empirically (2026-10-22, verified from a mainland China
# network) aiohttp's stack is fingerprinted by all three engines and answered
# with a JS-only shell: 360 dropped from ~900 KB of server-rendered results to
# a 94 KB skeleton, Baidu from ~1.4 MB to 22 KB, Sogou to its /antispider page.
# Identical headers over urllib get the fully rendered SERP every time, so the
# search path uses urllib; ``web_fetch`` keeps aiohttp/curl_cffi.
MAX_SERP_BYTES = 4 * 1024 * 1024

# One opener (cookie jar incl.) per engine host, reused for the process life.
# The homepage visit is not politeness theatre: without the cookies it sets
# (``BAIDUID``, ``SNUID``, ...) Baidu answers with a 1.4 KB stub and Sogou
# redirects to /antispider after a handful of cookie-less queries.
_SERP_OPENERS: Dict[str, Any] = {}
_SERP_OPENER_LOCK = threading.Lock()


def _serp_opener(host: str):
    """Return a cookie-keeping opener for ``host``, warming it up once."""
    with _SERP_OPENER_LOCK:
        opener = _SERP_OPENERS.get(host)
        if opener is not None:
            return opener
        handlers = [HTTPCookieProcessor(http.cookiejar.CookieJar())]
        if WEB_PROXY:
            handlers.append(ProxyHandler({"http": WEB_PROXY,
                                          "https": WEB_PROXY}))
        opener = build_opener(*handlers)
        try:
            warmup = Request("https://{}/".format(host),
                             headers={"User-Agent": random.choice(_USER_AGENTS),
                                      "Accept-Language": "zh-CN,zh;q=0.9"})
            with opener.open(warmup, timeout=SEARCH_TIMEOUT) as response:
                response.read(64 * 1024)
        except (HTTPError, URLError, OSError, ValueError) as exc:
            logger.debug("SERP warm-up for %s failed: %s", host, exc)
        _SERP_OPENERS[host] = opener
        return opener


def _decode_serp_body(raw: bytes, encoding: str, content_type: str) -> str:
    """Undo Content-Encoding and decode with the declared charset."""
    encoding = (encoding or "").lower()
    try:
        if "gzip" in encoding:
            raw = gzip.decompress(raw)
        elif "deflate" in encoding:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error):
        logger.debug("Could not undo Content-Encoding %r", encoding)
    charset_match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _host_allowed(url: str, allowed: tuple) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == name or host.endswith("." + name) for name in allowed)


def _fetch_serp_sync(url: str, host: str, language: str,
                     allowed_hosts: tuple) -> Optional[str]:
    """Blocking SERP GET (run via ``asyncio.to_thread``). ``None`` on failure."""
    headers = _search_headers(language, host)
    headers["Accept-Encoding"] = "gzip, deflate"  # urllib does not negotiate
    opener = _serp_opener(host)
    request = Request(url, headers=headers)
    try:
        with opener.open(request, timeout=SEARCH_TIMEOUT) as response:
            raw = response.read(MAX_SERP_BYTES)
            content_type = response.headers.get("Content-Type", "")
            final_url = response.geturl()
            encoding = response.headers.get("Content-Encoding", "")
    except HTTPError as exc:
        logger.warning("SERP request %s failed: HTTP %s", url, exc.code)
        return None
    except (URLError, OSError, ValueError) as exc:
        logger.warning("SERP request %s failed: %s", url, exc)
        return None
    # Redirects must stay on the engine's own hosts (anti-bot interstitials
    # live there); anything else is treated as a failure, never followed.
    if not _host_allowed(url, allowed_hosts) or not _host_allowed(
            final_url, allowed_hosts):
        logger.warning("SERP redirected off-engine: %s -> %s", url, final_url)
        return None
    return _decode_serp_body(raw, encoding, content_type)


async def _fetch_serp(url: str, host: str, language: str,
                      allowed_hosts: tuple) -> Optional[str]:
    return await asyncio.to_thread(
        _fetch_serp_sync, url, host, language, allowed_hosts)


def _attr_value(tag: str, name: str) -> str:
    """Read an attribute out of a raw opening tag (single/double quoted)."""
    match = re.search(
        r'\b{}\s*=\s*"([^"]*)"|\b{}\s*=\s*\'([^\']*)\''.format(name, name), tag)
    if not match:
        return ""
    return html.unescape(match.group(1) if match.group(1) is not None
                         else match.group(2)).strip()


def _clean_text(fragment: str) -> str:
    """Strip tags/entities and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _usable_url(url: str, blocked_hosts: tuple) -> bool:
    """Reject empty/relative/internal engine URLs (ads, navigation, wrappers)."""
    if not url.startswith(("http://", "https://")):
        return False
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return not any(host == blocked or host.endswith("." + blocked)
                   for blocked in blocked_hosts)


def _visible_text(fragment: str, title: str = "") -> str:
    """Readable text of an HTML fragment with scripts/styles/comments removed."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", fragment,
                  flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = _clean_text(text)
    if title:
        if text.startswith(title):
            text = text[len(title):].strip()
        else:
            text = text.replace(title, " ", 1).strip()
    return text


# -- Baidu ------------------------------------------------------------------

async def _search_baidu(query: str, max_results: int,
                        url_template: str = "") -> List[Dict[str, str]]:
    """Baidu web SERP; organic results carry the real URL in ``mu``."""
    # Ask for a few extra hits: rich cards are dropped, so ``rn`` must exceed
    # the wanted organic count.
    template = url_template or DEFAULT_ENGINE_URLS["baidu"]
    url = template.replace(
        "{count}", str(min(max_results + 4, 16)),
    ).replace("{query}", quote(query, safe=""))
    text = await _fetch_serp(url, "www.baidu.com", "zh-CN,zh;q=0.9,en;q=0.8",
                             ("baidu.com",))
    if not text:
        return []
    return _parse_baidu(text, max_results)


def _parse_baidu(html_text: str, max_results: int) -> List[Dict[str, str]]:
    """Extract organic ``.result.c-container`` blocks (``mu`` holds the URL)."""
    results: List[Dict[str, str]] = []
    starts = [m for m in re.finditer(
        r'<div[^>]*class="[^"]*\bresult\b[^"]*\bc-container\b[^"]*"[^>]*>',
        html_text, re.I)]
    for index, match in enumerate(starts):
        if len(results) >= max_results:
            break
        tag = match.group(0)
        if "result-op" in _attr_value(tag, "class"):
            continue
        url = _attr_value(tag, "mu")
        if not _usable_url(url, ("baidu.com",)):
            continue
        end = starts[index + 1].start() if index + 1 < len(starts) else len(html_text)
        block = html_text[match.end(): end]
        anchor = re.search(r"<h3[^>]*>.*?<a[^>]*>(.*?)</a>", block, re.I | re.S)
        if not anchor:
            continue
        title = _clean_text(anchor.group(1))
        if not title:
            continue
        results.append({"title": title, "url": url,
                        "snippet": _baidu_snippet(block, title)})
    return results


def _baidu_snippet(block: str, title: str) -> str:
    """Slice the abstract out of the block's visible text (title excluded)."""
    return _visible_text(block, title)[:300]


# -- 360 (so.com) -----------------------------------------------------------

async def _search_so360(query: str, max_results: int,
                        url_template: str = "") -> List[Dict[str, str]]:
    """360 search SERP; each result exposes the real URL as ``data-mdurl``."""
    template = url_template or DEFAULT_ENGINE_URLS["so360"]
    url = template.replace("{query}", quote(query, safe=""))
    text = await _fetch_serp(url, "www.so.com", "zh-CN,zh;q=0.9,en;q=0.8",
                             ("so.com",))
    if not text:
        return []
    return _parse_so360(text, max_results)


def _parse_so360(html_text: str, max_results: int) -> List[Dict[str, str]]:
    """Extract ``li.res-list`` blocks; ``data-mdurl`` is the real target."""
    results: List[Dict[str, str]] = []
    starts = [m for m in re.finditer(
        r'<li[^>]*class="[^"]*\bres-list\b[^"]*"[^>]*>', html_text, re.I)]
    for index, match in enumerate(starts):
        if len(results) >= max_results:
            break
        end = starts[index + 1].start() if index + 1 < len(starts) else len(html_text)
        block = html_text[match.end(): end]
        anchor = re.search(r"<h3[^>]*>.*?<a([^>]*)>(.*?)</a>", block, re.I | re.S)
        if not anchor:
            continue
        url = _attr_value(anchor.group(1), "data-mdurl")
        if not _usable_url(url, ("so.com", "360.cn", "360kan.com")):
            continue
        title = _clean_text(anchor.group(2))
        if not title:
            continue
        snippet = ""
        summary = re.search(
            r'<span[^>]*class="[^"]*res-list-summary[^"]*"[^>]*>(.*?)</span>',
            block, re.I | re.S)
        if not summary:
            summary = re.search(
                r'<p[^>]*class="[^"]*res-desc[^"]*"[^>]*>(.*?)</p>',
                block, re.I | re.S)
        if summary:
            snippet = _clean_text(summary.group(1))
        results.append({"title": title, "url": url, "snippet": snippet})
    return results


# -- Sogou ------------------------------------------------------------------

async def _search_sogou(query: str, max_results: int,
                        url_template: str = "") -> List[Dict[str, str]]:
    """Sogou SERP; links are ``/link?url=...`` JS redirects that need a hop."""
    template = url_template or DEFAULT_ENGINE_URLS["sogou"]
    url = template.replace("{query}", quote(query, safe=""))
    text = await _fetch_serp(url, "www.sogou.com", "zh-CN,zh;q=0.9,en;q=0.8",
                             ("sogou.com",))
    if not text:
        return []
    results = _parse_sogou(text, max_results)
    if results:
        results = await _resolve_sogou_links(results)
    return results


def _parse_sogou(html_text: str, max_results: int) -> List[Dict[str, str]]:
    """Extract ``div.vrwrap`` blocks; hrefs stay as ``/link?url=...`` for now."""
    results: List[Dict[str, str]] = []
    starts = [m for m in re.finditer(
        r'<div[^>]*class="[^"]*\bvrwrap\b[^"]*"[^>]*>', html_text, re.I)]
    for index, match in enumerate(starts):
        if len(results) >= max_results:
            break
        end = starts[index + 1].start() if index + 1 < len(starts) else len(html_text)
        block = html_text[match.end(): end]
        anchor = re.search(
            r'<h3[^>]*class="[^"]*vr-title[^"]*"[^>]*>.*?<a[^>]*href="([^"]+)"'
            r"[^>]*>(.*?)</a>", block, re.I | re.S)
        if not anchor:
            continue
        href = html.unescape(anchor.group(1)).strip()
        if not href:
            continue
        title = _clean_text(anchor.group(2))
        if not title:
            continue
        snippet = ""
        summary = re.search(
            r'id="cacheresult_summary[^"]*"[^>]*>(.*?)</div>', block, re.I | re.S)
        if summary:
            snippet = _clean_text(summary.group(1))
        if href.startswith("/"):
            href = urljoin("https://www.sogou.com", href)
        results.append({"title": title, "url": href, "snippet": snippet})
    return results


async def _resolve_sogou_links(
    results: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """Replace Sogou ``/link`` wrappers with their real target URLs."""
    semaphore = asyncio.Semaphore(4)

    async def one(item: Dict[str, str]) -> Optional[Dict[str, str]]:
        if "/link?" not in item["url"]:
            return item
        async with semaphore:
            real = await _resolve_sogou_link(item["url"])
        if not real:
            return None
        return {"title": item["title"], "url": real, "snippet": item["snippet"]}

    resolved = await asyncio.gather(*(one(item) for item in results))
    return [item for item in resolved if item]


async def _resolve_sogou_link(link: str) -> Optional[str]:
    """Read the JS-redirect stub Sogou serves for ``/link?url=...``."""
    text = await _fetch_serp(link, "www.sogou.com", "zh-CN,zh;q=0.9",
                             ("sogou.com",))
    if not text:
        return None
    match = re.search(r'location\.replace\(\s*["\']([^"\']+)["\']', text)
    if not match:
        match = re.search(r'URL=[\'"]([^\'"]+)[\'"]', text, re.I)
    if not match:
        return None
    target = html.unescape(match.group(1)).strip()
    if not target.startswith(("http://", "https://")):
        return None
    try:
        await validate_fetch_url(target)
    except UnsafeFetchTarget:
        return None
    return target


# -- Bing -------------------------------------------------------------------

async def _search_bing(query: str, max_results: int,
                       url_template: str = "") -> List[Dict[str, str]]:
    """Bing SERP; organic results are ``li.b_algo`` blocks."""
    template = url_template or DEFAULT_ENGINE_URLS["bing"]
    url = template.replace(
        "{count}", str(min(max_results + 4, 30)),
    ).replace("{query}", quote(query, safe=""))
    text = await _fetch_serp(url, "www.bing.com", "zh-CN,zh;q=0.9,en;q=0.8",
                             ("bing.com",))
    if not text:
        return []
    return _parse_bing(text, max_results)


def _parse_bing(html_text: str, max_results: int) -> List[Dict[str, str]]:
    """Extract ``li.b_algo`` blocks: ``h2 > a`` carries title + real URL."""
    results: List[Dict[str, str]] = []
    starts = [m for m in re.finditer(
        r'<li[^>]*class="[^"]*\bb_algo\b[^"]*"[^>]*>', html_text, re.I)]
    for index, match in enumerate(starts):
        if len(results) >= max_results:
            break
        end = starts[index + 1].start() if index + 1 < len(starts) else len(html_text)
        block = html_text[match.end(): end]
        anchor = re.search(r"<h2[^>]*>.*?<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
                           block, re.I | re.S)
        if not anchor:
            continue
        url = html.unescape(anchor.group(1)).strip()
        title = _clean_text(anchor.group(2))
        if not title or not url.startswith(("http://", "https://")):
            continue
        snippet_match = re.search(r"<p[^>]*>(.*?)</p>", block, re.I | re.S)
        snippet = _clean_text(snippet_match.group(1)) if snippet_match else ""
        results.append({"title": title, "url": url, "snippet": snippet[:300]})
    return results


# -- Google -----------------------------------------------------------------

async def _search_google(query: str, max_results: int,
                         url_template: str = "") -> List[Dict[str, str]]:
    """Google SERP; organic results are ``div.g`` blocks."""
    template = url_template or DEFAULT_ENGINE_URLS["google"]
    url = template.replace(
        "{count}", str(min(max_results + 4, 20)),
    ).replace("{query}", quote(query, safe=""))
    text = await _fetch_serp(url, "www.google.com", "zh-CN,zh;q=0.9,en;q=0.8",
                             ("google.com",))
    if not text:
        return []
    return _parse_google(text, max_results)


def _parse_google(html_text: str, max_results: int) -> List[Dict[str, str]]:
    """Extract ``div.g`` blocks: the first real ``<a href>`` plus its ``h3``."""
    results: List[Dict[str, str]] = []
    starts = [m for m in re.finditer(
        r'<div[^>]*class="[^"]*\bg\b[^"]*"[^>]*>', html_text, re.I)]
    for index, match in enumerate(starts):
        if len(results) >= max_results:
            break
        end = starts[index + 1].start() if index + 1 < len(starts) else len(html_text)
        block = html_text[match.end(): end]
        anchor = re.search(r'<a[^>]*href="(https?://[^"]+)"', block, re.I)
        title_match = re.search(r"<h3[^>]*>(.*?)</h3>", block, re.I | re.S)
        if not anchor or not title_match:
            continue
        url = html.unescape(anchor.group(1)).strip()
        title = _clean_text(title_match.group(1))
        if not title or not _usable_url(url, ("google.com",)):
            continue
        # Snippet lives in the .VwiC3b span (current layout); fall back to any
        # paragraph-ish text block.
        snippet_match = re.search(
            r'class="[^"]*VwiC3b[^"]*"[^>]*>(.*?)</', block, re.I | re.S)
        if not snippet_match:
            snippet_match = re.search(r"<span[^>]*>(.{40,}?)</span>", block, re.S)
        snippet = _clean_text(snippet_match.group(1)) if snippet_match else ""
        results.append({"title": title, "url": url, "snippet": snippet[:300]})
    return results


# -- Engine dispatch --------------------------------------------------------

async def _search_engine_once(spec: Dict[str, str], query: str,
                              max_results: int) -> List[Dict[str, str]]:
    """Run one configured entry: ``spec = {name(parser), label, url}``."""
    engine = spec.get("name", "")
    url_template = spec.get("url", "")
    if engine == "baidu":
        return await _search_baidu(query, max_results, url_template)
    if engine == "so360":
        return await _search_so360(query, max_results, url_template)
    if engine == "sogou":
        return await _search_sogou(query, max_results, url_template)
    if engine == "bing":
        return await _search_bing(query, max_results, url_template)
    if engine == "google":
        return await _search_google(query, max_results, url_template)
    return []


async def _search_engine(spec: Dict[str, str], query: str, max_results: int,
                         ) -> List[Dict[str, str]]:
    """One retry with jitter: engines sometimes answer 200 with a stub page."""
    for attempt in (0, 1):
        results = await _search_engine_once(spec, query, max_results)
        if results or attempt:
            return results
        await asyncio.sleep(random.uniform(0.5, 1.2))
    return []


def _results_relevant(results: List[Dict[str, str]], query: str) -> bool:
    """True if any result title/snippet shares a token with the query."""
    if not results:
        return False
    tokens = [t for t in re.findall(
        r"[A-Za-z0-9_\u4e00-\u9fff]+", query.lower()) if len(t) >= 2]
    if not tokens:
        return True
    for result in results:
        haystack = (result["title"] + " " + result["snippet"]).lower()
        if any(token in haystack for token in tokens):
            return True
    return False


def _relevance_score(results: List[Dict[str, str]], query: str) -> int:
    """Count query-token hits across all results (engine quality tie-break)."""
    tokens = [t for t in re.findall(
        r"[A-Za-z0-9_\u4e00-\u9fff]+", query.lower()) if len(t) >= 2]
    if not tokens:
        return len(results)
    score = 0
    for result in results:
        haystack = (result["title"] + " " + result["snippet"]).lower()
        score += sum(1 for token in tokens if token in haystack)
    return score


def _engine_available(key: str) -> bool:
    """Circuit breaker: skip an entry for 10 min after an irrelevant page."""
    return time.monotonic() > _engine_blocked_until.get(key, 0.0)


def _block_engine(key: str, label: str = "") -> None:
    _engine_blocked_until[key] = time.monotonic() + ENGINE_CIRCUIT_SECONDS
    logger.info("%s returned irrelevant results; circuit opened for %s s",
                label or key, ENGINE_CIRCUIT_SECONDS)


# Host suffix → parser. Lets a config entry omit ``name``: the parser is
# inferred from the link's own domain (a baidu.com link is parsed as Baidu).
HOST_PARSERS = (
    ("baidu.com", "baidu"),
    ("so.com", "so360"),
    ("sogou.com", "sogou"),
    ("bing.com", "bing"),
    ("google.com", "google"),
)


def _infer_parser(url: str) -> str:
    """Parser for a link's host, or "" when it is none of the known engines."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    for suffix, name in HOST_PARSERS:
        if host == suffix or host.endswith("." + suffix):
            return name
    return ""


def _parse_engine_spec(value: Any) -> List[Dict[str, str]]:
    """Parse the config value into ordered entries ``[{name, label, url}]``."""
    specs: List[Dict[str, str]] = []

    def add(name: str, label: str, url: str) -> None:
        if not url.startswith(("http://", "https://")) or "{query}" not in url:
            if url:
                logger.warning(
                    "Engine %s: URL %r invalid (must be http(s) with a "
                    "{query} placeholder); using the default", name, url)
            url = DEFAULT_ENGINE_URLS[name]
        if url in [s["url"] for s in specs]:
            return
        specs.append({"name": name, "label": label or name, "url": url})

    if isinstance(value, str):
        for part in value.split(","):
            name = part.strip().lower()
            if name in KNOWN_ENGINES:
                add(name, "", DEFAULT_ENGINE_URLS[name])
        return specs

    if isinstance(value, list):
        for entry in value:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip().lower()
            url_raw = str(entry.get("url", "") or "").strip()
            if not name:
                # A link on a known engine's domain needs no explicit parser.
                name = _infer_parser(url_raw)
            if not name:
                logger.warning(
                    "Search engine entry without a usable parser ignored "
                    "(link %r is not on a known engine domain; set 'name' to "
                    "one of %s)", url_raw, ", ".join(KNOWN_ENGINES))
                continue
            if name not in KNOWN_ENGINES:
                logger.warning("Unknown search engine %r ignored (known: %s)",
                               name, ", ".join(KNOWN_ENGINES))
                continue
            add(name, str(entry.get("label", "") or "").strip(),
                str(entry.get("url", "") or "").strip())
    return specs


def set_search_engines(value: Any) -> List[str]:
    """Apply the config value (called on load and on change)."""
    global _ACTIVE_SPECS
    specs = _parse_engine_spec(value)
    if not specs:
        specs = [{"name": n, "label": n, "url": DEFAULT_ENGINE_URLS[n]}
                 for n in KNOWN_ENGINES]
    _ACTIVE_SPECS = specs
    logger.info("Search engines set to: %s",
                ", ".join(s["label"] for s in specs))
    return _active_engines()


def _active_engines() -> List[str]:
    """Display labels of the active entries (priority order)."""
    return [s["label"] for s in _ACTIVE_SPECS]


def _active_specs() -> List[Dict[str, str]]:
    return [dict(s) for s in _ACTIVE_SPECS]


# ---------------------------------------------------------------------------
# Cache (.boarai/cache/web/, JSON, TTL 30 min, atomic writes)
# ---------------------------------------------------------------------------

def _cache_dir(project_root: Any) -> Path:
    # Data-dir anchor (home/.boarai by default) — NOT a tree-relative join:
    # the cache must follow --data-dir/BOARAI_DATA_DIR overrides.
    from boarai.core.runtime import get_data_dir

    return get_data_dir() / "cache" / "web"


def _cache_file(cache_dir: Path, url: str) -> Path:
    host = urlparse(url).hostname or "page"
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", host)[:60].strip("-") or "page"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return cache_dir / "{}-{}.json".format(slug, digest)


def _load_cache(cache_path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not cache_path.exists():
            return None
        if time.time() - cache_path.stat().st_mtime > CACHE_TTL:
            return None
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(data.get("text"), str):
            return data
    except Exception:
        logger.debug("Cache read failed for %s", cache_path, exc_info=True)
    return None


def _write_cache(cache_path: Path, url: str, title: str, text: str) -> None:
    tmp = cache_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(
        {"url": url, "title": title, "text": text}, ensure_ascii=False),
        encoding="utf-8")
    tmp.replace(cache_path)  # atomic


def _maybe_cleanup(cache_dir: Path) -> None:
    """Periodically evict expired cache files (every 25th write)."""
    global _cache_write_counter
    _cache_write_counter += 1
    if _cache_write_counter % 25 != 0:
        return
    cutoff = time.time() - CACHE_TTL
    try:
        for path in cache_dir.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        pass


def _window_result(url: str, title: str, text: str, max_chars: int,
                   offset: int, cached: bool = False,
                   tier: Optional[str] = None) -> Dict[str, Any]:
    """Build the truncate-and-store response window."""
    total = len(text)
    window = text[offset: offset + max_chars]
    chars_returned = len(window)
    truncated = offset + chars_returned < total
    result: Dict[str, Any] = {
        "url": url,
        "title": title,
        "content": window,
        "offset": offset,
        "chars_returned": chars_returned,
        "total_chars": total,
        "truncated": truncated,
    }
    if cached:
        result["cached"] = True
    if tier:
        result["fetch_tier"] = tier
    if truncated:
        result["note"] = (
            "Full page has {} chars; returned [{},{}). To read on, call "
            "web_fetch with the same url and offset={}.".format(
                total, offset, offset + chars_returned, offset + chars_returned))
    return result


# ---------------------------------------------------------------------------
# Tool registrations
# ---------------------------------------------------------------------------

@tool(
    name="web_search",
    description=(
        "Search the web without API keys (engines and their SERP links are "
        "configurable via the extension's settings, tried in priority order; "
        "works from mainland China). Returns a JSON list of "
        "{title, url, snippet} results. "
        "Use web_fetch to read the full content of a result URL."
    ),
    parameters=[
        ToolParameter(name="query", type="string",
                      description="Search query", required=True),
        ToolParameter(name="max_results", type="integer",
                      description="Max number of results (1-12)",
                      required=False, default=8),
    ],
    security_profile="none",
    compress_strategy="json",
    suggested_category="readonly",
)
async def web_search(ctx, query: str = "", max_results: int = 8) -> Dict[str, Any]:
    """Key-free web search over the configured engines (see the settings panel)."""
    try:
        query = str(query).strip()
    except Exception:
        query = ""
    if not query:
        return {"error": "Query cannot be empty"}
    try:
        max_results = min(max(int(max_results), 1), 12)
    except Exception:
        max_results = 8

    degraded: Optional[Dict[str, Any]] = None
    # Engines are tried STRICTLY in the configured priority order: the first
    # one returning relevant results wins. This makes the engine setting's
    # order meaningful (a parallel race would ignore it). The default order
    # (domestic engines first) therefore stops after Baidu when it answers —
    # one fewer request than the previous always-parallel race.
    for spec in _active_specs():
        label = spec["label"]
        if not _engine_available(spec["url"]):
            continue
        try:
            results = await _search_engine(spec, query, max_results)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s search failed: %s", label, exc)
            continue
        if _results_relevant(results, query):
            return {"query": query, "provider": label,
                    "count": len(results), "results": results}
        if results:
            _block_engine(spec["url"], label)
            degraded = degraded or {"provider": label, "results": results}

    if degraded:
        return {
            "query": query, "provider": degraded["provider"],
            "count": len(degraded["results"]), "results": degraded["results"],
            "note": "Results may be degraded (engine anti-bot filtering).",
        }
    return {"query": query, "provider": None, "count": 0, "results": [],
            "error": "No results from any search provider (network or anti-bot)."}


@tool(
    name="web_fetch",
    description=(
        "Fetch a URL as clean readable text (HTML stripped; handles JS-heavy "
        "pages and PDFs). Long pages are truncated — pass offset to continue "
        "reading without refetching (cached 30 min)."
    ),
    parameters=[
        ToolParameter(name="url", type="string",
                      description="Absolute HTTP(S) URL", required=True),
        ToolParameter(name="max_chars", type="integer",
                      description="Chars to return from the given offset",
                      required=False, default=DEFAULT_MAX_CHARS),
        ToolParameter(name="offset", type="integer",
                      description="Character offset to start reading from",
                      required=False, default=0),
    ],
    security_profile="none",
    compress_strategy="json",
    suggested_category="readonly",
)
async def web_fetch(ctx, url: str = "", max_chars: int = DEFAULT_MAX_CHARS,
                    offset: int = 0) -> Dict[str, Any]:
    """Fetch a URL to clean text; supports offset-based continuation."""
    url = str(url or "").strip()
    if not url:
        return {"error": "url is required"}
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"error": "URL must be http(s); got scheme %r" % parsed.scheme}
    if not parsed.netloc:
        return {"error": "URL must include a host"}
    try:
        # Validate before touching the URL cache as well as before every
        # network hop, so old cached private URLs cannot bypass the policy.
        await validate_fetch_url(url)
    except UnsafeFetchTarget as exc:
        return {"error": str(exc)}
    try:
        max_chars = min(max(int(max_chars), MIN_CHARS), MAX_CHARS_CAP)
        offset = max(int(offset), 0)
    except Exception:
        max_chars, offset = DEFAULT_MAX_CHARS, 0

    root = getattr(ctx, "project_root", None)
    if not root:
        return {"error": "Project root not set"}
    cache_dir = _cache_dir(root)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Cannot create cache dir %s: %s", cache_dir, exc)
        return {"error": "Cache directory unavailable: %s" % exc}
    cache_path = _cache_file(cache_dir, url)

    cached = _load_cache(cache_path)
    if cached is not None:
        title = cached.get("title") or url
        return _window_result(url, title, cached["text"], max_chars, offset,
                              cached=True)

    lock = _fetch_locks.setdefault(url, asyncio.Lock())
    async with lock:
        cached = _load_cache(cache_path)
        if cached is not None:
            title = cached.get("title") or url
            return _window_result(url, title, cached["text"], max_chars, offset,
                                  cached=True)
        title, text, error, tier = await fetch_url_chain(url)
        if error is not None:
            return error
        stored = (text or "")[:MAX_STORED_CHARS]
        try:
            _write_cache(cache_path, url, title or "", stored)
            _maybe_cleanup(cache_dir)
        except OSError as exc:
            logger.warning("Cache write failed for %s: %s", url, exc)
        return _window_result(url, title or "", stored, max_chars, offset,
                              tier=tier)


