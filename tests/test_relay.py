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


def test_requests_bypass_the_system_proxy(stub):
    assert "$req.Proxy = $null" in RELAY.read_text(encoding="ascii")   # direct LAN connection, no proxy in between
    msg = {"jsonrpc": "2.0", "id": 8, "method": "tools/list", "params": {}}
    out, err = run_relay(stub.url, [msg])   # and a request still works against the stub
    assert out_lines(out) == [{"jsonrpc": "2.0", "id": 8, "result": {"echo": msg}}]
    assert err == b""


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


def marker_reply(marker_text, is_error=False):
    def reply(req):
        out = {"jsonrpc": "2.0", "id": req["id"],
               "result": {"content": [{"type": "text", "text": marker_text}], "isError": is_error}}
        return 200, json.dumps(out).encode("utf-8")
    return reply


def marker(payload):
    return "DMX-RELAY " + json.dumps(payload, ensure_ascii=True)


def tool_call(name, id=5):
    return {"jsonrpc": "2.0", "id": id, "method": "tools/call", "params": {"name": name, "arguments": {}}}


def save_payload(file="Plan_p2.png", data=b"x", what="page 2", source="A/Plan.pdf"):
    return {"action": "save", "file": file, "what": what, "source": source,
            "data": base64.b64encode(data).decode()}


def run_marker(payload, call, tmp_path, *extra, is_error=False):
    s = Stub(marker_reply(marker(payload), is_error))
    try:
        out, err = run_relay(s.url, [call], "-ExportDir", str(tmp_path / "exp"), *extra)
    finally:
        s.close()
    return out, err


def first_text(out):
    return out_lines(out)[0]["result"]["content"][0]["text"]


def test_save_marker_writes_the_file_and_rewrites_the_text(tmp_path):
    data = b"\x89PNG fake image bytes " * 100
    s = Stub(marker_reply(marker(save_payload(data=data))))
    exports = tmp_path / "exp"
    call = tool_call("export_image")
    try:
        out1, err = run_relay(s.url, [call], "-ExportDir", str(exports))
        out2, _ = run_relay(s.url, [call], "-ExportDir", str(exports))
    finally:
        s.close()
    assert (exports / "Plan_p2.png").read_bytes() == data
    assert (exports / "Plan_p2_2.png").read_bytes() == data  # never overwrites
    assert first_text(out1) == (
        f"Saved page 2 of A/Plan.pdf to {exports / 'Plan_p2.png'} ({len(data) // 1024} KB), in the "
        f"local export folder {exports} on the user's PC. If you work in a sandboxed workspace "
        "that cannot see this folder, ask the user to add it to the workspace.")
    assert "Plan_p2_2.png" in first_text(out2)
    assert err == b""


def test_save_marker_never_writes_outside_the_export_folder(tmp_path):
    run_marker(save_payload(file="..\\..\\evil.png"), tool_call("export_image"), tmp_path)
    assert (tmp_path / "exp" / "evil.png").exists() and not (tmp_path / "evil.png").exists()


def test_save_marker_only_writes_image_files(tmp_path):
    out, _ = run_marker(save_payload(file="evil.xlsm"), tool_call("export_image"), tmp_path)
    r = out_lines(out)[0]["result"]
    assert r["isError"] is True and "image" in r["content"][0]["text"]
    assert not (tmp_path / "exp" / "evil.xlsm").exists()


def test_save_marker_caps_the_size(tmp_path):
    out, _ = run_marker(save_payload(data=b"\0" * (12 * 1024 * 1024 + 1)), tool_call("export_image"), tmp_path)
    assert out_lines(out)[0]["result"]["isError"] is True
    assert not (tmp_path / "exp" / "Plan_p2.png").exists()


def test_save_marker_with_french_names_round_trips(tmp_path):
    name = "Pr\u00e9hension_\u00e0_\u00e7.png"
    source = "A/Plan \u00ab pr\u00e9 \u00bb.pdf"
    out, _ = run_marker(save_payload(file=name, source=source, data=b"abc"), tool_call("export_image"), tmp_path)
    assert (tmp_path / "exp" / name).read_bytes() == b"abc"
    text = first_text(out)
    assert source in text and name in text
    assert source.encode("utf-8") in out and name.encode("utf-8") in out


@pytest.mark.parametrize("tool,payload", [
    ("search", save_payload()),                       # a marker in the answer to any other tool
    ("open_document", save_payload()),                # the wrong action for the tool
    ("export_image", {"action": "open", "path": "C:\\x.pdf", "ext": ".pdf", "page": None}),
])
def test_a_marker_is_only_obeyed_for_the_matching_tool(tmp_path, tool, payload):
    out, _ = run_marker(payload, tool_call(tool), tmp_path)
    assert first_text(out) == marker(payload)  # plain text, unchanged
    assert not (tmp_path / "exp").exists()


def test_a_marker_in_an_error_result_is_not_obeyed(tmp_path):
    out, _ = run_marker(save_payload(), tool_call("export_image"), tmp_path, is_error=True)
    assert first_text(out).startswith("DMX-RELAY ")
    assert not (tmp_path / "exp").exists()


def test_a_marker_without_a_pending_request_is_not_obeyed(tmp_path):
    """An answer whose id is not that of a forwarded export_image/open_document request."""
    def reply(req):
        out = {"jsonrpc": "2.0", "id": 999, "result": {"content": [{"type": "text", "text": marker(save_payload())}]}}
        return 200, json.dumps(out).encode("utf-8")
    s = Stub(reply)
    try:
        run_relay(s.url, [tool_call("export_image")], "-ExportDir", str(tmp_path / "exp"))
    finally:
        s.close()
    assert not (tmp_path / "exp").exists()


def test_a_redirect_is_an_error_not_followed():
    s = Stub(lambda req: (302, b""))
    try:
        out, _ = run_relay(s.url, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
    finally:
        s.close()
    assert out_lines(out)[0]["error"]["message"] == "dmx server error 302."


def launcher(tmp_path):
    """A .ps1 that records its arguments, one per line (UTF-8), in record.txt."""
    rec = tmp_path / "record.txt"
    ps1 = tmp_path / "launch.ps1"
    ps1.write_text("[System.IO.File]::AppendAllLines('" + str(rec) + "', [string[]]$args, "
                   "(New-Object System.Text.UTF8Encoding($false)))\r\n", encoding="ascii")
    return ps1, rec


def open_payload(path, ext, page):
    return {"action": "open", "path": str(path), "ext": ext, "page": page}


def make_doc(tmp_path, folder="dir with space", name="Offre.pdf"):
    f = tmp_path / folder / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"%PDF-1.4")
    return f


def run_open(tmp_path, payload, *extra):
    cmd, rec = launcher(tmp_path)
    s = Stub(marker_reply(marker(payload)))
    try:
        out, err = run_relay(s.url, [tool_call("open_document", 9)], "-OpenWith", str(cmd), *extra)
    finally:
        s.close()
    return out, rec


def test_open_marker_builds_a_quoted_acrobat_command_line_for_a_path_with_spaces(tmp_path):
    f = make_doc(tmp_path)
    out, rec = run_open(tmp_path, open_payload(f, ".pdf", 4))
    assert first_text(out) == f"Opened {f} at page 4."
    assert rec.read_text(encoding="utf-8").splitlines() == [f'acrobat.exe /A page=4 "{f}"']


def test_open_marker_default_application_gets_the_plain_path(tmp_path):
    f = make_doc(tmp_path, name="Liste.xlsx")
    out, rec = run_open(tmp_path, open_payload(f, ".xlsx", None))
    assert first_text(out) == f"Opened {f} ."
    assert rec.read_text(encoding="utf-8").splitlines() == [str(f)]


def test_open_marker_with_french_names_round_trips(tmp_path):
    f = make_doc(tmp_path, folder="dossier \u00e9t\u00e9 \u00e7", name="Offre \u00ab a \u00bb.pdf")
    out, rec = run_open(tmp_path, open_payload(f, ".pdf", 2))
    assert "dossier été ç".encode("utf-8") in out and "Offre « a ».pdf".encode("utf-8") in out
    assert first_text(out) == f"Opened {f} at page 2."
    assert rec.read_text(encoding="utf-8").splitlines() == [f'acrobat.exe /A page=2 "{f}"']


@pytest.mark.parametrize("bad", [
    "https://evil.example/x?y=.pdf",
    "search-ms:query=a&.pdf",
    "file:///C:/Windows/x.pdf",
    "C:\\a\\b\"/A x.pdf",
    "C:\\nope\\missing.pdf",       # does not exist
    "relative\\x.pdf",
    "C:\\a\\b.pdf:stream.pdf",
])
def test_open_marker_refuses_anything_but_an_existing_local_or_unc_file(tmp_path, bad):
    out, rec = run_open(tmp_path, open_payload(bad, ".pdf", 1))
    r = out_lines(out)[0]["result"]
    assert r["isError"] is True and "cannot be opened" in r["content"][0]["text"]
    assert not rec.exists()


def test_open_marker_refuses_a_program(tmp_path):
    f = make_doc(tmp_path, name="evil.exe")
    out, rec = run_open(tmp_path, open_payload(f, ".exe", None))
    assert not rec.exists()
    r = out_lines(out)[0]["result"]
    assert r["isError"] is True and "cannot be opened" in r["content"][0]["text"]


UNC_REFUSED = "the server host is not allowed"


def test_unc_allowlist_is_exact_and_refuses_before_any_lookup(tmp_path):
    unc = "\\\\fileserver.example\\share\\A\\Offre.pdf"
    for hosts in ("other1, other2", "fileserver", "example", "server.example"):  # no exact match
        out, rec = run_open(tmp_path, open_payload(unc, ".pdf", 1), "-AllowedHosts", hosts)
        assert UNC_REFUSED in first_text(out) and not rec.exists()


@pytest.mark.parametrize("hosts", ["", "${user_config.allowed_hosts}"])
def test_an_empty_or_unsubstituted_allowlist_refuses_every_unc_path(tmp_path, hosts):
    out, rec = run_open(tmp_path, open_payload("\\\\DMX-FS01\\share\\a.pdf", ".pdf", 1), "-AllowedHosts", hosts)
    assert UNC_REFUSED in first_text(out) and not rec.exists()


def test_an_empty_allowlist_still_opens_a_drive_path(tmp_path):
    f = make_doc(tmp_path)
    out, rec = run_open(tmp_path, open_payload(f, ".pdf", 1), "-AllowedHosts", "")
    assert first_text(out) == f"Opened {f} ."
    assert rec.read_text(encoding="utf-8").splitlines() == [str(f)]


@pytest.mark.parametrize("unc", [
    "\\\\?\\UNC\\DMX-FS01\\share\\a.pdf",
    "\\\\.\\C$\\a.pdf",
    "\\\\DMX-FS01@SSL\\share\\a.pdf",
    "\\\\DMX-FS01@8080\\share\\a.pdf",
    "\\\\DMX_FS01\\share\\a.pdf",
])
def test_unusual_unc_hosts_are_refused_even_when_listed(tmp_path, unc):
    out, rec = run_open(tmp_path, open_payload(unc, ".pdf", 1), "-AllowedHosts", "DMX-FS01,?,.,DMX-FS01@SSL,DMX_FS01")
    text = first_text(out)
    assert UNC_REFUSED in text or "unsafe characters" in text
    assert "was not found" not in text and not rec.exists()


def test_the_manifest_default_allows_the_file_server(tmp_path):
    m = json.loads((ROOT / "relay" / "manifest.template.json").read_text(encoding="utf-8"))
    d = m["user_config"]["allowed_hosts"]["default"]
    assert d == "DMX-FS01,DMX-FS01.rotzingerag.local"
    # the host passes to the existence check (the name does not resolve here: "not found", no program)
    out, rec = run_open(tmp_path, open_payload("\\\\dmx-fs01.\\share\\a.pdf", ".pdf", 1), "-AllowedHosts", d)
    assert UNC_REFUSED not in first_text(out) and not rec.exists()


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


def test_build_extensions_config_builds_every_world_of_the_config(tmp_path):
    import os
    cfg = tmp_path / "config.toml"
    cfg.write_text("[worlds.projects]\nprofile = 'projects'\n[worlds.offers]\nprofile = 'offers'\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_extensions.py"), "--config", str(cfg),
                        "--out", str(tmp_path / "out")], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert sorted(x.name for x in (tmp_path / "out").iterdir()) == ["dmx-offers.mcpb", "dmx-projects.mcpb"]
