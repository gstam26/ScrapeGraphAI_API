"""Local web UI: API contract tests against a live stdlib server on port 0,
with the pipeline stubbed — these test the UI plumbing, not extraction."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

import webapp.server as server


@pytest.fixture()
def ui(monkeypatch):
    """Fresh manager + live server on an ephemeral port; pipeline stubbed."""
    monkeypatch.setattr(server, "MANAGER", server.RunManager())

    done = threading.Event()

    def fake_run(input_path, output_name):
        print("stub pipeline running")
        done.wait(5)  # test controls completion
        out = f"outputs/{output_name}"
        import os
        os.makedirs("outputs", exist_ok=True)
        with open(out, "wb") as f:
            f.write(b"xlsx-bytes")
        return out

    monkeypatch.setattr(server, "execute_run", fake_run)
    httpd = server.serve(port=0, open_browser=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, done
    httpd.shutdown()


def _get(base, path):
    with urllib.request.urlopen(base + path) as r:
        return r.status, r.read()


def _post(base, path, body=b"", headers=None):
    req = urllib.request.Request(base + path, data=body, method="POST",
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_page_and_idle_status(ui):
    base, _ = ui
    code, body = _get(base, "/")
    assert code == 200 and b"Entity Extraction Pipeline" in body
    code, body = _get(base, "/api/status")
    assert json.loads(body)["state"] == "idle"


def test_upload_rejections(ui):
    base, _ = ui
    code, err = _post(base, "/api/run", b"", {"X-Filename": "a.xlsx"})
    assert code == 400
    code, err = _post(base, "/api/run", b"data", {"X-Filename": "notes.txt"})
    assert code == 400 and "template" in err["error"]


def test_run_lifecycle_and_second_run_conflict(ui):
    base, done = ui
    code, resp = _post(base, "/api/run", b"fake-xlsx", {"X-Filename": "in.xlsx"})
    assert code == 200 and resp["started"]

    # running: status shows the stub's log line; concurrent run is refused
    time.sleep(0.3)
    st = json.loads(_get(base, "/api/status")[1])
    assert st["state"] == "running"
    assert any("stub pipeline" in line for line in st["log"])
    code, err = _post(base, "/api/run", b"x", {"X-Filename": "b.xlsx"})
    assert code == 409

    # download before done is a 404, after done it serves the file
    with pytest.raises(urllib.error.HTTPError):
        _get(base, "/api/download")
    done.set()
    for _ in range(50):
        if json.loads(_get(base, "/api/status")[1])["state"] == "done":
            break
        time.sleep(0.1)
    code, body = _get(base, "/api/download")
    assert code == 200 and body == b"xlsx-bytes"


def test_failed_run_surfaces_error(ui, monkeypatch):
    base, _ = ui

    def boom(input_path, output_name):
        raise ValueError("urls sheet row 3: 'not a url' does not look like a URL")

    monkeypatch.setattr(server, "execute_run", boom)
    _post(base, "/api/run", b"fake", {"X-Filename": "in.xlsx"})
    for _ in range(50):
        st = json.loads(_get(base, "/api/status")[1])
        if st["state"] == "failed":
            break
        time.sleep(0.1)
    assert "row 3" in st["error"]


def test_template_download(ui):
    base, _ = ui
    code, body = _get(base, "/api/template")
    assert code == 200 and body[:2] == b"PK"  # xlsx = zip container
