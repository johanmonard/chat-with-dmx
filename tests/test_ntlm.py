"""NTLM sign-in middleware: a request reaches the app only after a completed NTLM handshake on its own
connection; every other request gets 401. The client side is sspi.ClientAuth in this process, so the
caller is the test machine's own login."""

import base64
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="windows only")

if sys.platform == "win32":
    import sspi
    from starlette.testclient import TestClient

    from dmx_docs.access import Caller
    from dmx_docs.ntlm import NtlmAuth


class EchoApp:
    """Answers with the caller's label (or "-" without a caller) and counts its calls."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, scope, receive, send):
        self.calls += 1
        caller = (scope.get("state") or {}).get("dmx_caller")
        body = (caller.label if caller is not None else "-").encode("utf-8")
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class CountingMaker:
    def __init__(self):
        self.calls = 0

    def __call__(self, token):
        self.calls += 1
        return Caller.from_token_handle(token, [])


def _client(app, port, host="10.0.0.5"):
    return TestClient(app, client=(host, port))


def _type1(ctx) -> str:
    _err, out = ctx.authorize(None)
    return "NTLM " + base64.b64encode(out[0].Buffer).decode("ascii")


def _type3(ctx, response) -> str:
    header = response.headers["www-authenticate"]
    scheme, _, data = header.partition(" ")
    assert scheme == "NTLM" and data
    _err, out = ctx.authorize(base64.b64decode(data))
    return "NTLM " + base64.b64encode(out[0].Buffer).decode("ascii")


def _handshake(client, path="/projects/mcp"):
    ctx = sspi.ClientAuth("NTLM")
    first = client.get(path, headers={"Authorization": _type1(ctx)})
    assert first.status_code == 401
    return client.get(path, headers={"Authorization": _type3(ctx, first)})


def _me() -> str:
    return Caller.from_token([]).label


def test_no_header_gets_401_ntlm():
    app = EchoApp()
    r = _client(NtlmAuth(app, CountingMaker()), 1111).get("/projects/mcp")
    assert r.status_code == 401
    assert r.headers.get_list("www-authenticate") == ["NTLM"]
    assert app.calls == 0


def test_other_schemes_get_401_ntlm():
    app = EchoApp()
    client = _client(NtlmAuth(app, CountingMaker()), 1111)
    for value in ("Negotiate TlRMTVNTUAABAAAAB4IIogAAAAAAAAAAAAAAAAAAAAAKAGFKAAAADw==",
                  "Basic dXNlcjpwYXNz", "NTLM", "NTLM ", "Bearer x"):
        r = client.get("/projects/mcp", headers={"Authorization": value})
        assert r.status_code == 401, value
        assert r.headers.get_list("www-authenticate") == ["NTLM"], value
    assert app.calls == 0


def test_type1_gets_a_challenge_and_not_the_app():
    app = EchoApp()
    r = _client(NtlmAuth(app, CountingMaker()), 1111).get(
        "/projects/mcp", headers={"Authorization": _type1(sspi.ClientAuth("NTLM"))})
    assert r.status_code == 401
    [header] = r.headers.get_list("www-authenticate")
    scheme, _, data = header.partition(" ")
    assert scheme == "NTLM"
    assert base64.b64decode(data)[:12] == b"NTLMSSP\x00\x02\x00\x00\x00"
    assert app.calls == 0


def test_full_handshake_reaches_the_app_as_me():
    app, maker = EchoApp(), CountingMaker()
    r = _handshake(_client(NtlmAuth(app, maker), 1111))
    assert r.status_code == 200
    assert r.text == _me()
    assert "\\" in r.text
    assert app.calls == 1 and maker.calls == 1


def test_garbage_token_gets_401():
    app = EchoApp()
    client = _client(NtlmAuth(app, CountingMaker()), 1111)
    garbage = ["NTLM !!!not-base64!!!",
               "NTLM " + base64.b64encode(b"hello world").decode(),
               "NTLM " + base64.b64encode(b"NTLMSSP\x00\x01\x00\x00\x00garbage").decode(),
               "NTLM " + base64.b64encode(b"NTLMSSP\x00\x03\x00\x00\x00garbage").decode(),
               "NTLM " + base64.b64encode(b"NTLMSSP\x00\x02\x00\x00\x00garbage").decode()]
    for value in garbage:
        r = client.get("/projects/mcp", headers={"Authorization": value})
        assert r.status_code == 401, value
        assert r.headers.get_list("www-authenticate") == ["NTLM"], value
    assert app.calls == 0


def test_anonymous_type3_gets_401():
    """A Type-3 with empty fields completes an ANONYMOUS LOGON context in SSPI: not a sign-in."""
    app, maker = EchoApp(), CountingMaker()
    client = _client(NtlmAuth(app, maker), 1111)
    r = client.get("/projects/mcp", headers={"Authorization": _type1(sspi.ClientAuth("NTLM"))})
    assert r.status_code == 401
    anonymous = "NTLM " + base64.b64encode(b"NTLMSSP\x00\x03\x00\x00\x00" + b"\x00" * 80).decode()
    r = client.get("/projects/mcp", headers={"Authorization": anonymous})
    assert r.status_code == 401
    assert r.headers.get_list("www-authenticate") == ["NTLM"]
    assert app.calls == 0 and maker.calls == 0


def test_type3_on_another_connection_gets_401():
    app = EchoApp()
    auth = NtlmAuth(app, CountingMaker())
    first, second = _client(auth, 1111), _client(auth, 2222)
    ctx = sspi.ClientAuth("NTLM")
    r = first.get("/projects/mcp", headers={"Authorization": _type1(ctx)})
    assert r.status_code == 401
    type3 = _type3(ctx, r)
    r = second.get("/projects/mcp", headers={"Authorization": type3})
    assert r.status_code == 401
    assert app.calls == 0
    # the pending context is not handed to another connection, and it is spent: no replay either
    r = first.get("/projects/mcp", headers={"Authorization": type3})
    assert r.status_code == 200
    r = first.get("/projects/mcp", headers={"Authorization": type3})
    assert r.status_code == 401


def test_type3_from_another_host_same_port_gets_401():
    app = EchoApp()
    auth = NtlmAuth(app, CountingMaker())
    ctx = sspi.ClientAuth("NTLM")
    r = _client(auth, 1111, host="10.0.0.5").get("/projects/mcp", headers={"Authorization": _type1(ctx)})
    r = _client(auth, 1111, host="10.0.0.6").get("/projects/mcp", headers={"Authorization": _type3(ctx, r)})
    assert r.status_code == 401
    assert app.calls == 0


def test_type3_replayed_after_success_gets_401():
    app = EchoApp()
    client = _client(NtlmAuth(app, CountingMaker()), 1111)
    ctx = sspi.ClientAuth("NTLM")
    r = client.get("/projects/mcp", headers={"Authorization": _type1(ctx)})
    type3 = _type3(ctx, r)
    assert client.get("/projects/mcp", headers={"Authorization": type3}).status_code == 200
    assert client.get("/projects/mcp", headers={"Authorization": type3}).status_code == 401
    assert app.calls == 1


def test_pending_context_expires_after_30_seconds():
    app, clock = EchoApp(), Clock()
    client = _client(NtlmAuth(app, CountingMaker(), clock=clock), 1111)
    ctx = sspi.ClientAuth("NTLM")
    r = client.get("/projects/mcp", headers={"Authorization": _type1(ctx)})
    clock.now += 31
    r = client.get("/projects/mcp", headers={"Authorization": _type3(ctx, r)})
    assert r.status_code == 401
    assert app.calls == 0


def test_pending_contexts_are_bounded():
    app = EchoApp()
    auth = NtlmAuth(app, CountingMaker(), max_pending=3)
    ctx = sspi.ClientAuth("NTLM")
    r = _client(auth, 1000).get("/projects/mcp", headers={"Authorization": _type1(ctx)})
    for port in range(1001, 1005):
        _client(auth, port).get("/projects/mcp", headers={"Authorization": _type1(sspi.ClientAuth("NTLM"))})
    assert auth.pending_count() <= 3
    # the oldest one was dropped: its Type-3 is refused
    r = _client(auth, 1000).get("/projects/mcp", headers={"Authorization": _type3(ctx, r)})
    assert r.status_code == 401
    assert app.calls == 0


def test_callers_are_cached_by_sid_for_cache_seconds():
    app, maker, clock = EchoApp(), CountingMaker(), Clock()
    client = _client(NtlmAuth(app, maker, cache_seconds=300, clock=clock), 1111)
    assert _handshake(client).text == _me()
    clock.now += 100
    assert _handshake(client).text == _me()
    assert maker.calls == 1
    clock.now += 301
    assert _handshake(client).text == _me()
    assert maker.calls == 2
    assert app.calls == 3


def test_the_default_domain_is_this_machines_ad_domain():
    """The machine's primary (AD) domain SID is the prefix of the test login's SID (a domain account)."""
    from dmx_docs import ntlm
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    me = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
    assert ntlm.primary_domain_sid() == me.rpartition("-")[0]


def test_a_login_outside_the_ad_domain_gets_401():
    """A VM-local account (or any other domain's) is refused: here the allowed domain is a fake one,
    so this machine's own domain login stands for the foreign account."""
    app, maker = EchoApp(), CountingMaker()
    r = _handshake(_client(NtlmAuth(app, maker, domain_sid="S-1-5-21-1-2-3"), 1111))
    assert r.status_code == 401
    assert r.headers.get_list("www-authenticate") == ["NTLM"]
    assert app.calls == 0 and maker.calls == 0


def test_no_ad_domain_known_gets_401(monkeypatch):
    """A machine in a workgroup, or an LSA that cannot be read: nobody signs in."""
    from dmx_docs import ntlm

    monkeypatch.setattr(ntlm, "primary_domain_sid", lambda: None)
    app, maker = EchoApp(), CountingMaker()
    r = _handshake(_client(NtlmAuth(app, maker), 1111))
    assert r.status_code == 401
    assert app.calls == 0 and maker.calls == 0


def test_concurrent_handshakes_on_their_own_connections():
    import threading

    app, maker = EchoApp(), CountingMaker()
    auth = NtlmAuth(app, maker)
    results = []

    def run(port):
        results.append(_handshake(_client(auth, port)).text)

    threads = [threading.Thread(target=run, args=(3000 + i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [_me()] * 8
    assert app.calls == 8
    assert auth.pending_count() == 0


def test_make_caller_failure_gets_401():
    app = EchoApp()

    def broken(token):
        raise RuntimeError("directory unreachable")

    r = _handshake(_client(NtlmAuth(app, broken), 1111))
    assert r.status_code == 401
    assert r.headers.get_list("www-authenticate") == ["NTLM"]
    assert app.calls == 0


def test_client_state_caller_is_never_trusted():
    """A caller already in the scope's state (set by nobody we know) is replaced or removed."""
    app = EchoApp()
    auth = NtlmAuth(app, CountingMaker())

    async def outer(scope, receive, send):
        scope = {**scope, "state": {"dmx_caller": Caller(frozenset(), True, "EVIL\\admin")}}
        await auth(scope, receive, send)

    client = _client(outer, 1111)
    assert client.get("/projects/mcp").status_code == 401
    assert client.get("/_admin/status").text == "-"
    assert _handshake(client).text == _me()


def test_admin_paths_bypass_ntlm():
    app = EchoApp()
    client = _client(NtlmAuth(app, CountingMaker()), 1111)
    r = client.get("/_admin/x")
    assert r.status_code == 200
    assert r.text == "-"
    assert app.calls == 1
    # only the /_admin/ prefix, not a look-alike
    assert client.get("/_adminx").status_code == 401
    assert client.get("/projects/_admin/x").status_code == 401
    assert app.calls == 1


def test_logs_never_contain_tokens(caplog):
    app = EchoApp()
    client = _client(NtlmAuth(app, CountingMaker()), 1111)
    ctx = sspi.ClientAuth("NTLM")
    with caplog.at_level("DEBUG", logger="dmx_docs.ntlm"):
        type1 = _type1(ctx)
        r = client.get("/projects/mcp", headers={"Authorization": type1})
        type3 = _type3(ctx, r)
        client.get("/projects/mcp", headers={"Authorization": type3})
        client.get("/projects/mcp", headers={"Authorization": "NTLM Z2FyYmFnZQ=="})
    text = caplog.text
    for secret in (type1[5:], type3[5:], r.headers["www-authenticate"][5:], "Z2FyYmFnZQ=="):
        assert secret not in text
