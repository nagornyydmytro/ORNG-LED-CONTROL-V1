"""Home Auto-show demonstration. Not an operator page.

    python scripts/auto_show_demo.py
    python scripts/auto_show_demo.py --dump

Isolated. No Art-Net, no venue USB, no deploy.
"""

from __future__ import annotations

import argparse
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from orng_led.audio.show_demo import dump_html, html_text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", type=str, default=None)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if args.dump is not None:
        path = dump_html(Path(args.dump))
        print(path)
        return
    payload = html_text().encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *values: object) -> None:
            sys.stderr.write("auto-show " + (fmt % values) + "\n")

        def do_GET(self) -> None:
            if self.path not in {"/", "/index.html", "/orng-auto-show.html"}:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Auto-show demo http://127.0.0.1:{args.port}/  (Ctrl+C to stop)")
    server.serve_forever()


if __name__ == "__main__":
    main()
