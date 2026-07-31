"""Local web front-end for the pipeline — stdlib only, by design.

Runs on localhost, single user, one pipeline run at a time. The browser
uploads an input workbook (raw bytes, no multipart — the one thing
`http.server` can't parse cleanly), the pipeline runs in a background
thread, the page polls /api/status for the live log, and the result
downloads when done.

Zero third-party web dependencies is deliberate: this ships to corporate
laptops where every new pip install is an IT negotiation. The stdlib
ThreadingHTTPServer is entirely sufficient for a two-screen localhost tool.
Swapping the front-end for React later touches nothing here — the JSON API
is the contract.
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_UPLOAD_DIR = os.path.join("outputs", "webapp_inputs")

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # a 69-entity input workbook is ~20 KB
_LOG_TAIL_LINES = 400


class _TeeStdout(io.TextIOBase):
    """Mirror pipeline prints to the real stdout AND an in-memory log."""

    def __init__(self, real, sink: list[str], lock: threading.Lock):
        self._real = real
        self._sink = sink
        self._lock = lock
        self._buf = ""

    def write(self, s: str) -> int:
        self._real.write(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            with self._lock:
                self._sink.append(line)
        return len(s)

    def flush(self) -> None:
        self._real.flush()


class RunManager:
    """One pipeline run at a time: idle -> running -> done | failed."""

    def __init__(self):
        self._lock = threading.Lock()
        self.state = "idle"
        self.log: list[str] = []
        self.error: str | None = None
        self.output_path: str | None = None
        self.output_name: str | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None

    def status(self) -> dict:
        with self._lock:
            elapsed = None
            if self.started_at:
                end = self.finished_at or time.time()
                elapsed = int(end - self.started_at)
            return {
                "state": self.state,
                "log": self.log[-_LOG_TAIL_LINES:],
                "error": self.error,
                "output_name": self.output_name,
                "elapsed_seconds": elapsed,
            }

    def start(self, input_path: str, output_name: str) -> None:
        with self._lock:
            if self.state == "running":
                raise RuntimeError("a run is already in progress")
            self.state = "running"
            self.log = []
            self.error = None
            self.output_path = None
            self.output_name = None
            self.started_at = time.time()
            self.finished_at = None
        threading.Thread(
            target=self._run, args=(input_path, output_name), daemon=True
        ).start()

    def _run(self, input_path: str, output_name: str) -> None:
        # Imported here so tests can patch webapp.server.execute_run with a
        # stub, and so importing this module never drags the pipeline in.
        tee = _TeeStdout(sys.stdout, self.log, self._lock)
        real_stdout, sys.stdout = sys.stdout, tee
        try:
            output_path = execute_run(input_path, output_name)
            with self._lock:
                self.output_path = output_path
                self.output_name = os.path.basename(output_path)
                self.state = "done"
        except Exception as e:  # surfaced to the UI, never a silent death
            with self._lock:
                self.error = str(e)
                self.state = "failed"
            traceback.print_exc()
        finally:
            sys.stdout = real_stdout
            with self._lock:
                self.finished_at = time.time()


def execute_run(input_path: str, output_name: str) -> str:
    """Read the workbook, run the pipeline, write the output. Returns path."""
    from config import OUTPUT_DIR
    from main import print_run_summary
    from pipeline import run_pipeline
    from src.io_excel import read_input, write_output_excel

    pipeline_input = read_input(input_path)
    if not pipeline_input.urls:
        raise ValueError("The urls sheet has no rows — nothing to crawl.")
    if not pipeline_input.columns:
        raise ValueError("The questions sheet has no rows — nothing to ask.")

    print(f"Loaded {len(pipeline_input.entities)} entities, "
          f"{len(pipeline_input.urls)} URLs, "
          f"{len(pipeline_input.columns)} questions")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, output_name)

    start = time.time()
    result, diag = run_pipeline(pipeline_input)
    write_output_excel(result, pipeline_input.columns, output_path, diag=diag)
    print_run_summary(result, pipeline_input, diag or {}, int(time.time() - start))
    return output_path


MANAGER = RunManager()


class Handler(BaseHTTPRequestHandler):
    server_version = "EntityPipelineUI/1.0"

    # ── helpers ──────────────────────────────────────────────────────────
    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: str, content_type: str, download_name: str | None = None) -> None:
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if download_name:
            self.send_header("Content-Disposition",
                             f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter default: no per-request spam
        pass

    # ── routes ───────────────────────────────────────────────────────────
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._file(os.path.join(_STATIC_DIR, "index.html"),
                       "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self._json(MANAGER.status())
        elif self.path == "/api/template":
            import subprocess
            import tempfile
            tmp = os.path.join(tempfile.gettempdir(), "pipeline_input_template.xlsx")
            subprocess.run([sys.executable, os.path.join("scripts", "build_input_template.py"), tmp],
                           check=True, capture_output=True)
            self._file(tmp, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       "input_template.xlsx")
        elif self.path == "/api/download":
            st = MANAGER.status()
            if st["state"] != "done" or not MANAGER.output_path:
                self._json({"error": "no finished run to download"}, 404)
                return
            self._file(MANAGER.output_path,
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       st["output_name"])
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/api/run":
            self._json({"error": "not found"}, 404)
            return

        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            self._json({"error": "empty upload"}, 400)
            return
        if length > _MAX_UPLOAD_BYTES:
            self._json({"error": "file too large for an input workbook"}, 413)
            return
        filename = os.path.basename(self.headers.get("X-Filename", "input.xlsx"))
        if not filename.lower().endswith(".xlsx"):
            self._json({"error": "the input must be a .xlsx workbook — "
                                 "download the template to get started"}, 400)
            return

        body = self.rfile.read(length)
        os.makedirs(_UPLOAD_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        input_path = os.path.join(_UPLOAD_DIR, f"{stamp}_{filename}")
        with open(input_path, "wb") as f:
            f.write(body)

        output_name = f"{os.path.splitext(filename)[0]}_results_{stamp}.xlsx"
        try:
            MANAGER.start(input_path, output_name)
        except RuntimeError as e:
            self._json({"error": str(e)}, 409)
            return
        self._json({"started": True, "output_name": output_name})


def serve(port: int = 8000, open_browser: bool = True) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"Entity Extraction Pipeline UI: {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    return httpd


def main() -> int:
    port = int(os.environ.get("PIPELINE_UI_PORT", "8000"))
    httpd = serve(port=port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0
