"""HTTP response compression.

WHY THIS EXISTS: the app shell is a 1.3 MB HTML document plus ~154 ES-module
subresources (~10 MB per cold load), and Hypercorn serves all of it UNCOMPRESSED over
HTTP/1.1. On a LAN that costs a couple of seconds; over a WAN it dominates the
login-to-welcome time. gzip takes the shell from 1.30 MB to 0.20 MB.

THE TWO RULES THAT KEEP THIS SAFE
---------------------------------
1. Never touch a body we do not fully own. Quart models a response body as one of
   DataBody (materialised bytes), FileBody (a path), IOBody or IterableBody (a
   generator). Every SSE endpoint in this app returns an IterableBody, and buffering
   one would break live streaming outright. So this compresses ONLY DataBody and
   FileBody, by explicit isinstance check — an allow-list, not a deny-list, so a body
   type added later is skipped rather than silently mangled.
2. Never spend the saving on the event loop. Compression is synchronous CPU on the
   single asyncio loop, which is exactly the pathology this work exists to remove.
   Dynamic bodies use level 1 (measured: 1.3 MB in 8.7 ms for 84% of the benefit of
   level 6). Static files use level 6 but are cached by (path, mtime, size), so the
   cost is paid once per file per process and every later request is a dict lookup.

Kill switch: set UDERIA_COMPRESSION=off to disable entirely (the hook then returns
every response untouched).
"""
from __future__ import annotations

import gzip
import logging
import os
import threading

logger = logging.getLogger("quart.app")

# Content types worth compressing. Anything absent is left alone — notably
# text/event-stream (SSE), images, fonts, audio and archives, which are either
# already compressed or must not be buffered.
COMPRESSIBLE_TYPES = frozenset({
    "text/html", "text/css", "text/plain", "text/markdown", "text/xml",
    "text/javascript", "application/javascript", "application/x-javascript",
    "application/json", "application/manifest+json", "application/xml",
    "image/svg+xml",
})

MIN_SIZE_BYTES = 1024          # below this, the headers cost more than the saving
LEVEL_DYNAMIC = 1              # per-request CPU — keep it cheap
LEVEL_STATIC = 6               # paid once per file, then cached

# Static-file cache: path -> (mtime_ns, size, gzipped_bytes). Bounded so a large
# static tree cannot grow the process without limit.
_CACHE_MAX_ENTRIES = 512
_CACHE_MAX_TOTAL_BYTES = 96 * 1024 * 1024
_cache: dict[str, tuple[int, int, bytes]] = {}
_cache_bytes = 0
_cache_lock = threading.Lock()


def is_enabled() -> bool:
    return os.environ.get("UDERIA_COMPRESSION", "on").strip().lower() not in {"off", "0", "false", "no"}


def _client_accepts_gzip(request) -> bool:
    return "gzip" in (request.headers.get("Accept-Encoding") or "").lower()


def _base_content_type(response) -> str:
    return (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()


def _cache_get(path: str, mtime_ns: int, size: int) -> bytes | None:
    with _cache_lock:
        hit = _cache.get(path)
    if hit and hit[0] == mtime_ns and hit[1] == size:
        return hit[2]
    return None


def _cache_put(path: str, mtime_ns: int, size: int, blob: bytes) -> None:
    global _cache_bytes
    with _cache_lock:
        old = _cache.get(path)
        if old:
            _cache_bytes -= len(old[2])
        if len(_cache) >= _CACHE_MAX_ENTRIES or _cache_bytes + len(blob) > _CACHE_MAX_TOTAL_BYTES:
            _cache.clear()
            _cache_bytes = 0
        _cache[path] = (mtime_ns, size, blob)
        _cache_bytes += len(blob)


def cache_stats() -> dict:
    with _cache_lock:
        return {"entries": len(_cache), "bytes": _cache_bytes}


def reset_cache() -> None:
    global _cache_bytes
    with _cache_lock:
        _cache.clear()
        _cache_bytes = 0


async def compress_response(request, response):
    """after_request hook. Returns the response, compressed in place when it is safe.

    Any unexpected condition returns the response UNCHANGED — a failure to compress is
    never allowed to become a failure to respond.
    """
    if not is_enabled():
        return response

    try:
        from quart.wrappers.response import DataBody, FileBody
    except Exception:                                     # pragma: no cover
        return response

    try:
        ctype = _base_content_type(response)
        if ctype not in COMPRESSIBLE_TYPES:
            return response

        # The representation now varies by Accept-Encoding — say so even when we do not
        # compress this particular response, or a shared cache can hand a gzipped body
        # to a client that never asked for one.
        vary = response.headers.get("Vary")
        if not vary:
            response.headers["Vary"] = "Accept-Encoding"
        elif "accept-encoding" not in vary.lower():
            response.headers["Vary"] = f"{vary}, Accept-Encoding"

        if response.status_code != 200:                   # 206 ranges, 304, errors
            return response
        if response.headers.get("Content-Encoding"):      # already encoded
            return response
        if request.headers.get("Range"):                  # a range of a gzip stream is meaningless
            return response
        if not _client_accepts_gzip(request):
            return response

        body = response.response
        blob: bytes | None = None

        if isinstance(body, DataBody):
            raw = bytes(body.data)
            if len(raw) < MIN_SIZE_BYTES:
                return response
            blob = gzip.compress(raw, LEVEL_DYNAMIC)

        elif isinstance(body, FileBody):
            # Only a whole file. A partial FileBody is a range read; leave it alone.
            if getattr(body, "begin", 0) != 0 or getattr(body, "end", None) != getattr(body, "size", None):
                return response
            path = str(body.file_path)
            st = os.stat(path)
            if st.st_size < MIN_SIZE_BYTES:
                return response
            blob = _cache_get(path, st.st_mtime_ns, st.st_size)
            if blob is None:
                with open(path, "rb") as fh:
                    blob = gzip.compress(fh.read(), LEVEL_STATIC)
                _cache_put(path, st.st_mtime_ns, st.st_size, blob)
        else:
            # IterableBody (every SSE endpoint), IOBody, or anything added later.
            return response

        if blob is None or len(blob) >= (response.content_length or len(blob) + 1):
            return response                               # compression did not help

        response.set_data(blob)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(blob))
        # Ranges no longer map onto a compressed body.
        response.headers.pop("Accept-Ranges", None)

        # The ETag is DELIBERATELY left alone. Suffixing it ("...-gzip") to mark the
        # compressed entity looks correct and is a real regression: the framework
        # evaluates If-None-Match against the ORIGINAL entity tag long before this hook
        # runs, so a client echoing the suffixed tag never matches and every
        # revalidation returns a full 200 instead of a 304 — turning a warm reload into
        # a cold one. `Vary: Accept-Encoding` (set above) is the correct and sufficient
        # signal that the representation differs by encoding, and is what shared caches
        # key on. Verified live: with the suffix a gzip revalidation returned 200; with
        # it removed it returns 304.
        return response

    except Exception as exc:                              # never break a response
        logger.warning(f"Response compression skipped: {exc}")
        return response
