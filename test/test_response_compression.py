"""Gate for src/trusted_data_agent/core/compression.py.

WHY THIS EXISTS: compression is an after_request hook that touches EVERY response in
the app. The failure that matters is not "a page is a bit larger" — it is buffering an
SSE stream and breaking live execution, or mangling a range/304/already-encoded
response. This drives the SHIPPED hook with real Quart body objects.

Pure: no server, no network.
"""
import asyncio
import gzip
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from quart.wrappers.response import Response, DataBody, FileBody, IterableBody  # noqa: E402
from trusted_data_agent.core import compression  # noqa: E402

_pass = _fail = 0


def check(name, cond, detail=""):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print(f"  FAIL: {name}" + (f" — {detail}" if detail else ""))


class FakeRequest:
    def __init__(self, accept="gzip, deflate, br", range_hdr=None):
        self.headers = {}
        if accept is not None:
            self.headers["Accept-Encoding"] = accept
        if range_hdr:
            self.headers["Range"] = range_hdr


def run(req, resp):
    return asyncio.run(compression.compress_response(req, resp))


BIG_HTML = ("<html><body>" + ("<p>the quick brown fox</p>" * 4000) + "</body></html>").encode()

# ── §1 the win: a large HTML body is compressed and round-trips exactly ──────
r = Response(BIG_HTML, content_type="text/html")
out = run(FakeRequest(), r)
check("a large text/html body is gzipped", out.headers.get("Content-Encoding") == "gzip")
raw = bytes(out.response.data)
check("the compressed body decompresses to the ORIGINAL bytes", gzip.decompress(raw) == BIG_HTML)
check("Content-Length matches the compressed body", out.headers.get("Content-Length") == str(len(raw)))
check("compression actually shrank it", len(raw) < len(BIG_HTML) / 2,
      f"{len(raw)} vs {len(BIG_HTML)}")
check("Vary announces Accept-Encoding", "accept-encoding" in (out.headers.get("Vary") or "").lower())

# ── §2 THE CRITICAL ONE: an SSE / streaming body is never touched ────────────
async def _gen():
    yield b"data: one\n\n"
    yield b"data: two\n\n"

r = Response(_gen(), content_type="text/event-stream")
out = run(FakeRequest(), r)
check("SSE is NOT compressed", out.headers.get("Content-Encoding") is None)
check("SSE body object is untouched (still an IterableBody)", isinstance(out.response, IterableBody))

# an IterableBody carrying a compressible content type must ALSO be left alone:
# the body type is the rule, not the content type.
r = Response(_gen(), content_type="application/json")
out = run(FakeRequest(), r)
check("a streamed JSON body is NOT compressed (body type wins)", out.headers.get("Content-Encoding") is None)
check("streamed JSON stays an IterableBody", isinstance(out.response, IterableBody))

# ── §3 negative cases: every guard ───────────────────────────────────────────
r = Response(BIG_HTML, content_type="text/html")
out = run(FakeRequest(accept="deflate"), r)
check("a client that does not accept gzip gets raw bytes", out.headers.get("Content-Encoding") is None)
check("...but Vary is still set for caches", "accept-encoding" in (out.headers.get("Vary") or "").lower())

r = Response(b"tiny", content_type="text/html")
out = run(FakeRequest(), r)
check("a body under the threshold is not compressed", out.headers.get("Content-Encoding") is None)

r = Response(os.urandom(50000), content_type="image/png")
out = run(FakeRequest(), r)
check("a non-compressible content type is skipped", out.headers.get("Content-Encoding") is None)
check("...and gets no Vary header", out.headers.get("Vary") is None)

r = Response(BIG_HTML, content_type="text/html", status=304)
out = run(FakeRequest(), r)
check("a 304 is never compressed", out.headers.get("Content-Encoding") is None)

r = Response(BIG_HTML, content_type="text/html", status=206)
out = run(FakeRequest(), r)
check("a 206 partial response is never compressed", out.headers.get("Content-Encoding") is None)

r = Response(BIG_HTML, content_type="text/html")
out = run(FakeRequest(range_hdr="bytes=0-100"), r)
check("a Range request is never compressed", out.headers.get("Content-Encoding") is None)

r = Response(gzip.compress(BIG_HTML), content_type="text/html")
r.headers["Content-Encoding"] = "gzip"
out = run(FakeRequest(), r)
check("an already-encoded body is not double-compressed",
      gzip.decompress(bytes(out.response.data)) == BIG_HTML)

# ── §4 static files: compressed, cached by mtime, and invalidated on change ──
compression.reset_cache()
with tempfile.TemporaryDirectory() as td:
    fp = os.path.join(td, "app.js")
    payload = ("function x(){return 'hello world';}\n" * 3000).encode()
    with open(fp, "wb") as fh:
        fh.write(payload)

    r = Response(FileBody(fp), content_type="text/javascript")
    out = run(FakeRequest(), r)
    check("a static file body is compressed", out.headers.get("Content-Encoding") == "gzip")
    check("the static file round-trips exactly", gzip.decompress(bytes(out.response.data)) == payload)
    check("the compressed static file is cached", compression.cache_stats()["entries"] == 1)

    r2 = Response(FileBody(fp), content_type="text/javascript")
    out2 = run(FakeRequest(), r2)
    check("a second request serves identical bytes from cache",
          bytes(out2.response.data) == bytes(out.response.data))
    check("cache did not grow on a hit", compression.cache_stats()["entries"] == 1)

    # THE DEPLOY TRAP: editing the file must invalidate the cache, or a deploy serves
    # stale JS forever.
    new_payload = ("function y(){return 'goodbye world';}\n" * 3000).encode()
    os.utime(fp, ns=(0, 0))
    with open(fp, "wb") as fh:
        fh.write(new_payload)
    os.utime(fp, ns=(10**18, 10**18))
    r3 = Response(FileBody(fp), content_type="text/javascript")
    out3 = run(FakeRequest(), r3)
    check("a changed file is re-compressed, not served stale",
          gzip.decompress(bytes(out3.response.data)) == new_payload)

    # a partial FileBody is a range read
    fb = FileBody(fp)
    fb.begin, fb.end = 10, 100
    r4 = Response(fb, content_type="text/javascript")
    out4 = run(FakeRequest(), r4)
    check("a partial (range) FileBody is not compressed", out4.headers.get("Content-Encoding") is None)

# ── §4b the ETag must survive compression untouched ─────────────────────────
# REGRESSION FOUND IN LIVE TESTING: suffixing the ETag ("...-gzip") to mark the
# compressed entity looks correct and silently breaks caching. The framework evaluates
# If-None-Match against the ORIGINAL tag before this hook runs, so a client echoing the
# suffixed tag never matches and every revalidation returns a full 200 instead of 304 —
# a warm reload becomes a cold one. Vary: Accept-Encoding is the correct signal.
r = Response(BIG_HTML, content_type="text/html")
r.headers["ETag"] = '"abc123"'
out = run(FakeRequest(), r)
check("compression happened for the ETag case", out.headers.get("Content-Encoding") == "gzip")
check("the ETag is passed through UNCHANGED (or a 304 can never match)",
      out.headers.get("ETag") == '"abc123"', f"got {out.headers.get('ETag')!r}")

# ── §5 the kill switch ───────────────────────────────────────────────────────
os.environ["UDERIA_COMPRESSION"] = "off"
r = Response(BIG_HTML, content_type="text/html")
out = run(FakeRequest(), r)
check("UDERIA_COMPRESSION=off disables the hook entirely", out.headers.get("Content-Encoding") is None)
check("...and leaves the body byte-identical", bytes(out.response.data) == BIG_HTML)
os.environ.pop("UDERIA_COMPRESSION")
r = Response(BIG_HTML, content_type="text/html")
out = run(FakeRequest(), r)
check("removing the switch re-enables it", out.headers.get("Content-Encoding") == "gzip")

# ── §6 a broken response can never break the request ─────────────────────────
# NOTE: this MUST subclass DataBody. An unrelated object falls through the body-type
# allow-list to the `else` branch and returns without ever entering the try/except —
# the assertion would then pass for the wrong reason and could never catch a narrowed
# handler. (Found by mutation-testing this very gate.)
class ExplodingBody(DataBody):
    def __init__(self):
        pass  # deliberately skip DataBody.__init__, which would assign .data

    @property
    def data(self):
        raise RuntimeError("boom")

r = Response(BIG_HTML, content_type="text/html")
r.response = ExplodingBody()
try:
    out = run(FakeRequest(), r)
    check("a body that raises is returned untouched, not propagated", out is r)
    check("...and carries no bogus Content-Encoding", out.headers.get("Content-Encoding") is None)
except Exception as exc:
    # A gate that DIES on a mutation instead of NAMING it is half a gate.
    check("a body that raises is returned untouched, not propagated", False,
          f"the exception escaped the hook: {type(exc).__name__}: {exc}")
    check("...and carries no bogus Content-Encoding", False, "hook raised")

print(f"\nresponse compression gate: {_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
