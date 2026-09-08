#!/usr/bin/env python3
"""Serve web/ and accept POST /result — so the probe page can report its verdict
without a DevTools bridge. Writes each POST to result.json and prints it.

Usage: python3 ci/serve.py [port] [webdir]
"""
import json
import sys
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
WEBDIR = sys.argv[2] if len(sys.argv) > 2 else "web"


class H(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self):
        if self.path.split("?")[0] != "/result":
            return self._send(404)
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            obj = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            obj = {"_raw": raw.decode("utf-8", "replace")}
        tag = "FINAL" if obj.get("done") else "interim"
        with open("result.json", "w") as f:
            json.dump(obj, f, indent=1)
        # A compact line to the CI log for each report.
        print("POST /result [%s]: %s" % (tag, json.dumps({
            k: obj.get(k) for k in ("phase", "playPromiseA", "playPromiseB",
                                    "playedNoGesture", "playedAfterGesture",
                                    "readyForGesture", "reproduced", "done")})), flush=True)
        self._send(200, b"ok")

    def do_GET(self):
        path = self.path.split("?")[0].lstrip("/")
        if path == "":
            path = "autoplay.html"
        fp = os.path.join(WEBDIR, path)
        if not os.path.isfile(fp):
            return self._send(404, b"not found")
        ctype = ("text/html" if fp.endswith(".html")
                 else "application/javascript" if fp.endswith(".js")
                 else "application/octet-stream")
        with open(fp, "rb") as f:
            self._send(200, f.read(), ctype)

    def log_message(self, *a):
        pass  # quiet; we print our own lines


if __name__ == "__main__":
    print("serving %s on 0.0.0.0:%d (POST /result)" % (WEBDIR, PORT), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
