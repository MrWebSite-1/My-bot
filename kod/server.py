"""PING SERVER + RELAY: Render uxlab qolmasligi uchun ping (GET).
GET /anim: jonlantirish hodisalari (J1, X-Anim-Secret bilan).
POST /relay: B variantda ikkinchi botdan xabar qabul qiladi (yashirin kalit bilan).
"""
import asyncio, hmac, json, os
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Ping(BaseHTTPRequestHandler):
    def _cors(self):
        """CORS faqat /anim uchun va faqat ANIM_ORIGIN domeniga."""
        from config import ANIM_ORIGIN
        if ANIM_ORIGIN and self.path.split("?")[0] == "/anim":
            self.send_header("Access-Control-Allow-Origin", ANIM_ORIGIN)
            self.send_header("Access-Control-Allow-Headers", "X-Anim-Secret")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Vary", "Origin")

    def do_OPTIONS(self):
        self._reply(204, b"")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/anim":
            return self._anim(u)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    do_HEAD = do_GET

    def _anim(self, u):
        """GET /anim?since=<id>: yangi hodisalar (X-Anim-Secret kerak, env ANIM_SECRET)."""
        from config import ANIM_SECRET
        if not ANIM_SECRET:
            return self._reply(404, b"no")
        got = self.headers.get("X-Anim-Secret") or ""
        if not hmac.compare_digest(got.encode(), ANIM_SECRET.encode()):
            return self._reply(403, b"no")
        try:
            since = int((parse_qs(u.query).get("since") or ["0"])[0])
        except ValueError:
            since = 0
        import anim
        events, last = anim.get_since(since)
        body = json.dumps({"last": last, "events": events},
                          ensure_ascii=False).encode()
        self._reply(200, body, "application/json; charset=utf-8")

    def _reply(self, code, body, ctype=None):
        self.send_response(code)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split("?")[0] != "/relay":
            return self._reply(404, b"no")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > 20000:
            return self._reply(400, b"bad")
        body = self.rfile.read(n)
        from config import RELAY_SECRET
        got = self.headers.get("X-Relay-Secret") or ""
        if not RELAY_SECRET or not hmac.compare_digest(
                got.encode(), RELAY_SECRET.encode()):
            return self._reply(403, b"no")
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError
        except Exception:
            return self._reply(400, b"bad")
        import relay
        if not relay.READY:
            return self._reply(503, b"wait")
        asyncio.run_coroutine_threadsafe(relay.on_relay(data), relay.LOOP)
        self._reply(200, b"ok")

    def log_message(self, *a):
        pass

def run_web():
    port = int(os.environ.get("PORT", 10000))
    ThreadingHTTPServer(("0.0.0.0", port), Ping).serve_forever()
