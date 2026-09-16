#!/usr/bin/env python3
r"""
A throwaway local webhook receiver — see exactly what the scraper POSTs,
without needing an n8n instance yet.

    # terminal 1
    python webhook_sink.py

    # terminal 2 — a FRESH output dir, so every job counts as new and the
    # webhook actually fires (the scraper stays silent when nothing is new)
    python scraper.py --limit 5 --append --out "$env:TEMP\webtest" `
      --webhook http://127.0.0.1:9000 --webhook-token dev-secret

Ctrl-C to stop. Prints the headers n8n's Header Auth would check, then the body.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 9000
TOKEN_HEADER = "X-Webhook-Token"


class Sink(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")

        print(f"\n{'=' * 62}")
        print(f"POST {self.path}")
        print(f"  {TOKEN_HEADER}: {self.headers.get(TOKEN_HEADER) or '(absent)'}")
        print(f"  Content-Type: {self.headers.get('Content-Type')}")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            print("  body was not JSON:")
            print(raw[:2000])
        else:
            print(f"  run_at:           {body.get('run_at')}")
            print(f"  scraped:          {body.get('scraped')}")
            print(f"  new:              {body.get('new')}")
            print(f"  skipped_existing: {body.get('skipped_existing')}")
            jobs = body.get("jobs") or []
            print(f"  jobs:             {len(jobs)}")
            for j in jobs[:5]:
                print(f"    - [{j.get('job_id')}] {j.get('title')}")
                print(f"      {j.get('wage')}  |  {j.get('url')}")
            if len(jobs) > 5:
                print(f"    … and {len(jobs) - 5} more")
        print("=" * 62, flush=True)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args) -> None:
        """Silence the default per-request line; we print our own."""


if __name__ == "__main__":
    print(f"listening on http://127.0.0.1:{PORT}  (Ctrl-C to stop)", flush=True)
    try:
        HTTPServer(("127.0.0.1", PORT), Sink).serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
        sys.exit(0)
