"""NTLM sign-in for the central server: an ASGI middleware that lets a request reach the app only after a
completed NTLM handshake, and hands the app the caller of that Windows login.

The handshake is connection-bound (the client sends Type-1, gets the challenge, then sends Type-3 on the
same TCP connection): the pending server context is kept per (client host, client port) for at most
PENDING_SECONDS and is never matched to another connection. Every request is signed in on its own; a
completed login is not remembered for the connection.

Fail closed: a missing or foreign Authorization header, a malformed or refused message, a Type-3
without its pending context, an error while building the caller - all give 401 and the app is not
called. Every path is signed in, /_admin/ included (the admin routes are on their own listener, central.py).

Logs name the client address and the outcome, never header values or tokens.
"""

from __future__ import annotations

import base64
import binascii
import logging
import threading
import time
from collections import OrderedDict
from typing import Callable

import anyio.to_thread

log = logging.getLogger("dmx_docs.ntlm")

PENDING_SECONDS = 30.0
MAX_PENDING = 1024        # handshakes in progress; beyond it the oldest is dropped
MAX_CALLERS = 1024        # cached callers; beyond it the least recently signed in is dropped
MAX_HEADER = 16384        # longest Authorization header accepted (NTLM messages are a few hundred bytes)

_SIGNATURE = b"NTLMSSP\x00"
_NEGOTIATE, _CHALLENGE, _AUTHENTICATE = 1, 2, 3


class _Refused(Exception):
    """The request is not signed in; the text says why (for the log, never a token)."""


def _message_type(blob: bytes) -> int:
    if len(blob) < 12 or blob[:8] != _SIGNATURE:
        raise _Refused("not an NTLM message")
    return int.from_bytes(blob[8:12], "little")


def _dispose(server) -> None:
    """Free an sspi.ServerAuth's context and credentials handles now rather than at garbage collection."""
    ctxt = getattr(server, "ctxt", None)
    if ctxt is not None:
        try:
            ctxt.DeleteSecurityContext()
        except Exception:
            pass
    creds = getattr(server, "credentials", None)
    if creds is not None:
        try:
            creds.FreeCredentialsHandle()
        except Exception:
            pass


_ANONYMOUS = "S-1-5-7"
_AUTHENTICATED_USERS = "S-1-5-11"
_GUESTS = "S-1-5-32-546"


def primary_domain_sid() -> str | None:
    """SID of the AD domain this machine is joined to, from the local LSA (no domain controller needed);
    None for a workgroup machine or when the LSA cannot be read."""
    import win32security

    try:
        policy = win32security.LsaOpenPolicy(None, win32security.POLICY_VIEW_LOCAL_INFORMATION)
        try:
            _name, sid = win32security.LsaQueryInformationPolicy(
                policy, win32security.PolicyPrimaryDomainInformation)
        finally:
            win32security.LsaClose(policy)
    except Exception as e:
        log.warning("cannot read this machine's AD domain: %s", type(e).__name__)
        return None
    return win32security.ConvertSidToStringSid(sid) if sid is not None else None


def _signed_in_user(token, domain_sid: str) -> str:
    """The user SID of a completed login's token. Only accounts of the machine's AD domain sign in:
    a local account of this machine (or another domain's) cannot read the file server, so it must not
    read here either. NTLM also completes an anonymous login (a Type-3 with empty fields) and may map
    a failed one to Guest; neither is a sign-in here. Windows puts Authenticated Users in every token
    except anonymous and Guest ones, so it is required."""
    import win32security

    sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
    if sid == _ANONYMOUS:
        raise _Refused("anonymous login")
    if not sid.startswith("S-1-5-21-") or sid.rpartition("-")[0] != domain_sid:
        raise _Refused("not an account of the AD domain")
    if sid.startswith("S-1-5-21-") and sid.rpartition("-")[2] == "501":
        raise _Refused("guest login")
    groups = {win32security.ConvertSidToStringSid(g): attrs
              for g, attrs in win32security.GetTokenInformation(token, win32security.TokenGroups)}
    if not groups.get(_AUTHENTICATED_USERS, 0) & win32security.SE_GROUP_ENABLED:
        raise _Refused("login is not an authenticated user")
    if _GUESTS in groups:
        raise _Refused("guest login")
    return sid


class NtlmAuth:
    """ASGI middleware: NTLM sign-in in front of `app`. On success the caller built by
    make_caller(token handle) is in scope["state"]["dmx_caller"]. Callers are cached by user SID for
    cache_seconds, so their group lookups are not repeated on every request."""

    def __init__(self, app, make_caller: Callable[[object], object], cache_seconds: float = 300,
                 clock: Callable[[], float] = time.monotonic, max_pending: int = MAX_PENDING,
                 pending_seconds: float = PENDING_SECONDS, domain_sid: str | None = None):
        """domain_sid: the AD domain whose accounts may sign in; None reads this machine's primary
        domain (primary_domain_sid) on the first sign-in. No domain known: every login is refused."""
        self.app = app
        self._domain_sid = domain_sid
        self.make_caller = make_caller
        self.cache_seconds = cache_seconds
        self._clock = clock
        self._max_pending = max(1, max_pending)
        self._pending_seconds = pending_seconds
        self._pending: OrderedDict[tuple[str, int], tuple[float, object]] = OrderedDict()  # -> (deadline, ServerAuth)
        self._pending_lock = threading.Lock()
        self._callers: OrderedDict[str, tuple[float, object]] = OrderedDict()  # user SID -> (expiry, caller)
        self._callers_lock = threading.Lock()

    # -- ASGI ---------------------------------------------------------------------------------------

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind == "lifespan":
            await self.app(scope, receive, send)
            return
        if kind != "http":
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        state = dict(scope.get("state") or {})
        state.pop("dmx_caller", None)       # only this middleware puts a caller there
        try:
            caller, challenge = await self._sign_in(scope)
        except _Refused as e:
            log.info("NTLM sign-in refused for %s: %s", self._who(scope), e)
            await self._unauthorized(send, None)
            return
        except Exception as e:  # never let a failed sign-in become a 500 or reach the app
            log.warning("NTLM sign-in failed for %s: %s", self._who(scope), type(e).__name__)
            await self._unauthorized(send, None)
            return
        if caller is None:
            await self._unauthorized(send, challenge)
            return
        state["dmx_caller"] = caller
        await self.app({**scope, "state": state}, receive, send)

    @staticmethod
    def _who(scope) -> str:
        client = scope.get("client")
        return f"{client[0]}:{client[1]}" if client else "unknown client"

    @staticmethod
    async def _unauthorized(send, challenge: bytes | None) -> None:
        value = b"NTLM" if challenge is None else b"NTLM " + base64.b64encode(challenge)
        body = b"Not signed in."
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"www-authenticate", value),
                                (b"content-type", b"text/plain; charset=utf-8"),
                                (b"content-length", str(len(body)).encode("ascii"))]})
        await send({"type": "http.response.body", "body": body})

    # -- handshake ----------------------------------------------------------------------------------

    async def _sign_in(self, scope) -> tuple[object | None, bytes | None]:
        """(caller, None) when signed in, (None, challenge) after a Type-1; raises _Refused otherwise."""
        client = scope.get("client")
        if not client or client[0] is None or client[1] is None:
            raise _Refused("no client address")
        key = (str(client[0]), int(client[1]))
        values = [v for k, v in scope.get("headers") or [] if k.lower() == b"authorization"]
        if not values:
            raise _Refused("no Authorization header")
        if len(values) > 1:
            raise _Refused("several Authorization headers")
        if len(values[0]) > MAX_HEADER:
            raise _Refused("Authorization header too long")
        scheme, _, data = values[0].strip().partition(b" ")
        if scheme.lower() != b"ntlm" or not data.strip():
            raise _Refused("not an NTLM Authorization header")
        try:
            blob = base64.b64decode(data.strip(), validate=True)
        except (binascii.Error, ValueError):
            raise _Refused("NTLM message is not base64") from None
        kind = _message_type(blob)
        if kind == _NEGOTIATE:
            return None, await anyio.to_thread.run_sync(self._start, key, blob)
        if kind == _AUTHENTICATE:
            server = self._take_pending(key)
            if server is None:
                raise _Refused("no pending handshake on this connection")
            return await anyio.to_thread.run_sync(self._finish, server, blob), None
        raise _Refused(f"unexpected NTLM message type {kind}")

    def _start(self, key: tuple[str, int], blob: bytes) -> bytes:
        """Type-1: a new server context answers with the challenge and waits for this connection's Type-3."""
        import sspi
        import sspicon

        server = sspi.ServerAuth("NTLM")
        try:
            err, out = server.authorize(blob)
            challenge = out[0].Buffer if out else b""
            if err != sspicon.SEC_I_CONTINUE_NEEDED or not challenge:
                raise _Refused("NTLM negotiate message refused")
        except _Refused:
            _dispose(server)
            raise
        except Exception:
            _dispose(server)
            raise _Refused("NTLM negotiate message refused") from None
        self._put_pending(key, server)
        return bytes(challenge)

    def _finish(self, server, blob: bytes):
        """Type-3: complete the context, read the client's token, build (or reuse) its caller."""
        import sspicon

        try:
            try:
                err, _out = server.authorize(blob)
            except Exception:
                raise _Refused("NTLM authenticate message refused") from None
            if err != sspicon.SEC_E_OK:
                raise _Refused("NTLM handshake not complete")
            token = server.ctxt.QuerySecurityContextToken()
            try:
                sid = _signed_in_user(token, self._allowed_domain())
                caller = self._cached_caller(sid)
                if caller is None:
                    caller = self.make_caller(token)
                    if caller is None:
                        raise _Refused("no caller for this login")
                    self._remember_caller(sid, caller)
            finally:
                token.Close()
        finally:
            _dispose(server)
        log.debug("NTLM sign-in: %s", getattr(caller, "label", ""))
        return caller

    def _allowed_domain(self) -> str:
        """The AD domain SID whose accounts sign in, read once (a failed read is retried next time)."""
        if self._domain_sid is None:
            self._domain_sid = primary_domain_sid()
        if not self._domain_sid:
            raise _Refused("this machine's AD domain is unknown")
        return self._domain_sid

    # -- pending contexts -----------------------------------------------------------------------------

    def _put_pending(self, key: tuple[str, int], server) -> None:
        dropped = []
        now = self._clock()
        with self._pending_lock:
            old = self._pending.pop(key, None)
            if old is not None:
                dropped.append(old[1])
            dropped.extend(self._expire_pending(now))
            while len(self._pending) >= self._max_pending:
                dropped.append(self._pending.popitem(last=False)[1][1])
            self._pending[key] = (now + self._pending_seconds, server)
        for s in dropped:
            _dispose(s)

    def _take_pending(self, key: tuple[str, int]):
        """The pending context of this connection, removed (a context completes once), or None."""
        now = self._clock()
        with self._pending_lock:
            hit = self._pending.pop(key, None)
            dropped = self._expire_pending(now)
        if hit is not None and hit[0] < now:
            dropped.append(hit[1])
            hit = None
        for s in dropped:
            _dispose(s)
        return hit[1] if hit is not None else None

    def _expire_pending(self, now: float) -> list:
        """Remove the expired contexts (caller holds the lock); returns them for disposal."""
        expired = [k for k, (deadline, _s) in self._pending.items() if deadline < now]
        return [self._pending.pop(k)[1] for k in expired]

    def pending_count(self) -> int:
        with self._pending_lock:
            return len(self._pending)

    # -- caller cache -------------------------------------------------------------------------------

    def _cached_caller(self, sid: str):
        now = self._clock()
        with self._callers_lock:
            hit = self._callers.get(sid)
            if hit is None:
                return None
            if hit[0] < now:
                del self._callers[sid]
                return None
            return hit[1]

    def _remember_caller(self, sid: str, caller) -> None:
        now = self._clock()
        with self._callers_lock:
            self._callers[sid] = (now + self.cache_seconds, caller)
            self._callers.move_to_end(sid)
            for k in [k for k, (expiry, _c) in self._callers.items() if expiry < now]:
                del self._callers[k]
            while len(self._callers) > MAX_CALLERS:
                self._callers.popitem(last=False)
