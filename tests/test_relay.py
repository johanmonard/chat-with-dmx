"""The relay extension (relay/dmx-relay.ps1): run as a real PowerShell process against a stub HTTP server
(no auth) for its stdin/stdout behaviour, and the .mcpb builder."""

import base64
import json
import shutil
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from test_central import two_worlds  # noqa: F401  (fixture)

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELAY = ROOT / "relay" / "dmx-relay.ps1"

pytestmark = pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell.exe"),
                                reason="the relay is a Windows PowerShell script")


class Stub:
    """http.server in a thread; reply(request dict) -> (status, body bytes)."""

    def __init__(self, reply):
        self.requests = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def handle(self):
                try:
                    super().handle()
                except ConnectionError:  # the relay exits with its keep-alive connection open
                    pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                stub.requests.append({"path": self.path, "headers": dict(self.headers), "raw": raw})
                status, body = reply(json.loads(raw.decode("utf-8")))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                body = b'{"caller": "DMX-x", "admin": false}'
                stub.requests.append({"path": self.path, "headers": dict(self.headers), "raw": b""})
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/projects/mcp"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def echo(req):
    if "id" not in req:
        return 202, b""
    out = {"jsonrpc": "2.0", "id": req["id"], "result": {"echo": req}}
    return 200, json.dumps(out, ensure_ascii=False).encode("utf-8")


def run_relay(url, lines, *extra, timeout=90):
    """Feed `lines` (dicts) to the relay, close stdin, return (stdout bytes, stderr bytes)."""
    data = b"".join(json.dumps(m, ensure_ascii=False).encode("utf-8") + b"\n" for m in lines)
    p = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(RELAY),
                        "-Url", url, *extra], input=data, capture_output=True, timeout=timeout)
    return p.stdout, p.stderr


def out_lines(stdout: bytes) -> list[dict]:
    assert not stdout.startswith(b"\xef\xbb\xbf")
    text = stdout.decode("utf-8")
    return [json.loads(x) for x in text.split("\n") if x]


@pytest.fixture
def stub():
    s = Stub(echo)
    yield s
    s.close()


@pytest.fixture
def stub_401():
    s = Stub(lambda req: (401, b'{"error": "no"}'))
    yield s
    s.close()


def test_request_and_response_pass_through_unchanged(stub):
    msg = {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}}
    out, err = run_relay(stub.url, [msg])
    assert out_lines(out) == [{"jsonrpc": "2.0", "id": 7, "result": {"echo": msg}}]
    assert err == b""
    req = stub.requests[0]
    assert req["path"] == "/projects/mcp"
    assert json.loads(req["raw"]) == msg
    assert req["headers"]["Content-Type"] == "application/json"
    assert req["headers"]["Accept"] == "application/json, text/event-stream"


def test_french_text_round_trips(stub):
    text = "pr\u00e9henseur \u00e0 ventouses \u00ab \u00e7 \u00bb"
    msg = {"jsonrpc": "2.0", "id": "a", "method": "tools/call", "params": {"arguments": {"query": text}}}
    out, _ = run_relay(stub.url, [msg])
    assert json.loads(stub.requests[0]["raw"].decode("utf-8"))["params"]["arguments"]["query"] == text
    assert out_lines(out)[0]["result"]["echo"]["params"]["arguments"]["query"] == text
    assert text.encode("utf-8") in out  # real UTF-8 on stdout, not escapes


def test_notification_writes_nothing(stub):
    out, _ = run_relay(stub.url, [{"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert out == b""
    assert len(stub.requests) == 1


def test_unreachable_server_gives_a_jsonrpc_error_with_the_id():
    out, _ = run_relay("http://127.0.0.1:1/projects/mcp", [{"jsonrpc": "2.0", "id": 3, "method": "ping"}])
    [r] = out_lines(out)
    assert r["id"] == 3 and r["error"]["message"] == (
        "The dmx server (127.0.0.1) cannot be reached. Check the network or ask IT.")


def test_401_gives_the_login_message(stub_401):
    out, _ = run_relay(stub_401.url, [{"jsonrpc": "2.0", "id": "x", "method": "ping"}])
    [r] = out_lines(out)
    assert r["id"] == "x" and r["error"]["message"] == "Your Windows login was not accepted by the dmx server."


def test_other_http_error_gives_the_code():
    s = Stub(lambda req: (503, b"busy"))
    try:
        out, _ = run_relay(s.url, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
    finally:
        s.close()
    assert out_lines(out)[0]["error"]["message"] == "dmx server error 503."


def marker_reply(marker_text):
    def reply(req):
        out = {"jsonrpc": "2.0", "id": req["id"],
               "result": {"content": [{"type": "text", "text": marker_text}], "isError": False}}
        return 200, json.dumps(out).encode("utf-8")
    return reply


def marker(payload):
    return "DMX-RELAY " + json.dumps(payload, ensure_ascii=True)


def test_save_marker_writes_the_file_and_rewrites_the_text(tmp_path):
    data = b"\x89PNG fake image bytes " * 100
    payload = {"action": "save", "file": "Plan_p2.png", "what": "page 2", "source": "A/Plan.pdf",
               "data": base64.b64encode(data).decode()}
    s = Stub(marker_reply(marker(payload)))
    exports = tmp_path / "exp"
    call = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "export_image"}}
    try:
        out1, err = run_relay(s.url, [call], "-ExportDir", str(exports))
        out2, _ = run_relay(s.url, [call], "-ExportDir", str(exports))
    finally:
        s.close()
    assert (exports / "Plan_p2.png").read_bytes() == data
    assert (exports / "Plan_p2_2.png").read_bytes() == data  # never overwrites
    text = out_lines(out1)[0]["result"]["content"][0]["text"]
    assert text == (f"Saved page 2 of A/Plan.pdf to {exports / 'Plan_p2.png'} ({len(data) // 1024} KB), in the "
                    f"local export folder {exports} on the user's PC. If you work in a sandboxed workspace "
                    "that cannot see this folder, ask the user to add it to the workspace.")
    assert "Plan_p2_2.png" in out_lines(out2)[0]["result"]["content"][0]["text"]
    assert err == b""


def test_save_marker_never_writes_outside_the_export_folder(tmp_path):
    payload = {"action": "save", "file": "..\\..\\evil.png", "what": "page 1", "source": "x",
               "data": base64.b64encode(b"x").decode()}
    s = Stub(marker_reply(marker(payload)))
    exports = tmp_path / "a" / "b"
    try:
        run_relay(s.url, [{"jsonrpc": "2.0", "id": 1, "method": "tools/call"}], "-ExportDir", str(exports))
    finally:
        s.close()
    assert (exports / "evil.png").exists() and not (tmp_path / "evil.png").exists()


def launcher(tmp_path):
    """A .cmd that records its arguments, one per line, in record.txt."""
    rec = tmp_path / "record.txt"
    cmd = tmp_path / "launch.cmd"
    cmd.write_text('@echo off\r\n:loop\r\nif "%~1"=="" goto end\r\n>>"' + str(rec)
                   + '" echo %~1\r\nshift\r\ngoto loop\r\n:end\r\n')
    return cmd, rec


@pytest.mark.parametrize("ext,page", [(".pdf", 4), (".xlsx", None)])
def test_open_marker_calls_the_launcher(tmp_path, ext, page):
    cmd, rec = launcher(tmp_path)
    path = "\\\\srv\\share\\A\\Offre" + ext
    s = Stub(marker_reply(marker({"action": "open", "path": path, "ext": ext, "page": page})))
    try:
        out, err = run_relay(s.url, [{"jsonrpc": "2.0", "id": 9, "method": "tools/call"}], "-OpenWith", str(cmd))
    finally:
        s.close()
    text = out_lines(out)[0]["result"]["content"][0]["text"]
    assert text.startswith(f"Opened {path} ") and text.endswith(".")
    assert "DMX-RELAY" not in text
    lines = rec.read_text().splitlines()
    assert path in lines
    if page:
        assert lines[1:4] == ["/A", "page", str(page)]  # Acrobat /A page=N (cmd.exe splits at "=")


def test_open_marker_refuses_a_program(tmp_path):
    cmd, rec = launcher(tmp_path)
    s = Stub(marker_reply(marker({"action": "open", "path": "C:\\x\\evil.exe", "ext": ".exe", "page": None})))
    try:
        out, _ = run_relay(s.url, [{"jsonrpc": "2.0", "id": 9, "method": "tools/call"}], "-OpenWith", str(cmd))
    finally:
        s.close()
    assert not rec.exists()
    r = out_lines(out)[0]["result"]
    assert r["isError"] is True and "cannot be opened" in r["content"][0]["text"]


def test_whoami_prints_the_answer(stub):
    p = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(RELAY),
                        "-Url", stub.url, "-WhoAmI"], capture_output=True, timeout=60)
    assert json.loads(p.stdout.decode("utf-8"))["admin"] is False
    assert stub.requests[0]["path"] == "/_whoami"


def test_build_extensions_writes_one_mcpb_per_world(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_extensions
    paths = build_extensions.build(["projects", "offers"], tmp_path)
    assert [p.name for p in paths] == ["dmx-projects.mcpb", "dmx-offers.mcpb"]
    with zipfile.ZipFile(paths[0]) as z:
        assert sorted(z.namelist()) == ["manifest.json", "server/dmx-relay.ps1"]
        assert z.read("server/dmx-relay.ps1") == RELAY.read_bytes()
        m = json.loads(z.read("manifest.json"))
    assert m["name"] == "dmx-projects"
    cfg = m["server"]["mcp_config"]
    assert cfg["command"] == "powershell.exe"
    assert cfg["args"][:4] == ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    assert cfg["args"][4] == "${__dirname}/server/dmx-relay.ps1"
    assert cfg["args"][-2:] == ["-Url", "${user_config.server_url}/projects/mcp"]
    assert m["user_config"]["server_url"]["default"] == "http://dmx-ws-vm01:8765"


def test_relay_signs_in_with_ntlm_against_the_central_server(two_worlds):
    """The real central app (auth=True) in uvicorn: the relay's NTLM handshake over one keep-alive
    connection, as the logged-on user, then a tools/list and /_whoami."""
    import socket
    import time

    import uvicorn

    from dmx_docs import central

    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(central.build_app(two_worlds, ["projects"], auth=True),
                                           host="127.0.0.1", port=port, log_level="warning"))
    server.install_signal_handlers = lambda: None
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.1)
        assert server.started
        url = f"http://127.0.0.1:{port}/projects/mcp"
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        out, err = run_relay(url, msgs, timeout=120)
        who = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(RELAY),
                              "-Url", url, "-WhoAmI"], capture_output=True, timeout=60)
    finally:
        server.should_exit = True
        t.join(10)
    answers = out_lines(out)
    assert [a["id"] for a in answers] == [1, 2], (answers, err)
    assert all("search" in {x["name"] for x in a["result"]["tools"]} for a in answers)
    assert "caller" in json.loads(who.stdout.decode("utf-8"))
