"""A person signs in to the board service (`docs/decisions.md` ADR 324).

Users with a salted scrypt password hash and a TOTP secret, opaque server-side
sessions in an HttpOnly cookie, a CSRF check on every write a session makes,
roles that gate writes, and an append-only audit log. Stdlib only. All of it
lives in the directory `BOARD_AUTH_DIR` names: unset, sign-in is off and the
service behaves as it did before. The machine path (bearers) is not touched:
a request with no session cookie passes the hook here untouched.

Never logged and never served: a password, a code, a password hash, a TOTP
secret, a session id (ADR 324, 10). `_now` is the only clock (ADR 324, 11).
"""
from __future__ import annotations

import base64
import contextlib
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import struct

from flask import Flask, g, jsonify, request

AUTH_DIR_ENV = "BOARD_AUTH_DIR"
COOKIE = "harness_session"
CSRF_HEADER = "X-CSRF-Token"
SESSION_LIFETIME = 12 * 3600
SESSION_IDLE = 3600
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 5
SCRYPT_MAXMEM = 64 * 1024 * 1024
ROLES = ("owner", "reviewer")
# Flask endpoint names of `app.py`: the reviewer's only writes besides sign-out.
REVIEWER_WRITES = frozenset({"session_delete", "http_comment"})
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
PASSWORD_MIN, PASSWORD_MAX = 12, 1024
TOTP_STEP = 30

_FAILED = "sign-in failed"
_NO_SESSION = "the session has expired or does not exist"
_dummy_hash: str | None = None


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(when: dt.datetime) -> str:
    return when.isoformat()


def _parse(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text)


# --- passwords ---------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM, dklen=64,
    )
    b64 = lambda raw: base64.b64encode(raw).decode()  # noqa: E731
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, want = stored.split("$")
        if scheme != "scrypt":
            return False
        want_raw = base64.b64decode(want)
        digest = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
            maxmem=SCRYPT_MAXMEM, dklen=len(want_raw),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, want_raw)


def _dummy() -> str:
    # What an unknown name is checked against, so it is not answered faster.
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(16))
    return _dummy_hash


# --- TOTP (RFC 6238: HMAC-SHA1, 6 digits, 30 s) --------------------------------


def _hotp(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    number = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 10**6:06d}"


def _step(at: dt.datetime) -> int:
    return int(at.timestamp()) // TOTP_STEP


def totp_code(secret_b32: str, at: dt.datetime) -> str:
    return _hotp(secret_b32, _step(at))


def _match_step(secret_b32: str, code: str, at: dt.datetime) -> int | None:
    """The highest step within one either side of `at` that `code` is, or None."""
    found = None
    for step in (_step(at) + 1, _step(at), _step(at) - 1):
        if hmac.compare_digest(_hotp(secret_b32, step), code):
            found = step if found is None else max(found, step)
    return found


# --- the store ---------------------------------------------------------------


def _ensure_dir(auth_dir: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(auth_dir)), exist_ok=True)
    try:
        os.mkdir(auth_dir, 0o700)
    except FileExistsError:
        return
    os.chmod(auth_dir, 0o700)


def _create_private(path: str) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


@contextlib.contextmanager
def _locked(auth_dir: str):
    _ensure_dir(auth_dir)
    lock = os.path.join(auth_dir, ".lock")
    _create_private(lock)
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # releases the flock


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (FileNotFoundError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _write(path: str, doc: dict) -> None:
    tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(doc, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _users_path(auth_dir: str) -> str:
    return os.path.join(auth_dir, "users.json")


def _sessions_path(auth_dir: str) -> str:
    return os.path.join(auth_dir, "sessions.json")


def create_user(auth_dir: str, name: str, password: str, role: str) -> str:
    """Add a user and return the TOTP secret, once. `ValueError` says what is wrong."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError("a name is 1 to 64 characters of a-z, 0-9, _, . and -, not starting with a symbol")
    if role not in ROLES:
        raise ValueError(f"a role is one of {', '.join(ROLES)}")
    if not isinstance(password, str) or not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise ValueError(f"a password is {PASSWORD_MIN} to {PASSWORD_MAX} characters")
    secret = base64.b32encode(secrets.token_bytes(20)).decode()
    record = {
        "role": role,
        "password": hash_password(password),
        "totp_secret": secret,
        "totp_last_step": 0,
        "failures": 0,
        "locked_until": None,
        "created_at": _iso(_now()),
    }
    with _locked(auth_dir):
        users = _read(_users_path(auth_dir))
        if name in users:
            raise ValueError(f"the user {name!r} already exists")
        users[name] = record
        _write(_users_path(auth_dir), users)
    return secret


# --- the audit log -----------------------------------------------------------


def _audit_path(auth_dir: str) -> str:
    return os.path.join(auth_dir, "audit.jsonl")


def audit(auth_dir: str, event: str, user: str | None = None, **fields) -> None:
    """Append one line. `user` is a name only when it names a user."""
    line = {"at": _iso(_now()), "event": event, "user": user}
    if request:
        line["addr"] = request.remote_addr
        forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if forwarded:
            line["forwarded_for"] = forwarded
    line.update({k: v for k, v in fields.items() if v is not None})
    _ensure_dir(auth_dir)
    path = _audit_path(auth_dir)
    _create_private(path)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(fd, (json.dumps(line, sort_keys=True) + "\n").encode())
    finally:
        os.close(fd)


def read_audit(auth_dir: str) -> list[dict]:
    try:
        with open(_audit_path(auth_dir), encoding="utf-8") as handle:
            raw = handle.read().splitlines()
    except FileNotFoundError:
        return []
    lines = []
    for text in raw:
        try:
            doc = json.loads(text)
        except ValueError:
            continue
        if isinstance(doc, dict):
            lines.append(doc)
    return lines


# --- sessions ----------------------------------------------------------------


def _sid_key(sid: str) -> str:
    return hashlib.sha256(sid.encode()).hexdigest()


def _live(session: dict, now: dt.datetime) -> bool:
    try:
        return (
            now < _parse(session["expires_at"])
            and (now - _parse(session["last_seen"])).total_seconds() <= SESSION_IDLE
        )
    except (KeyError, ValueError, TypeError):
        return False


def _prune(sessions: dict, now: dt.datetime) -> dict:
    return {key: val for key, val in sessions.items() if _live(val, now)}


def _view(session: dict, role: str) -> dict:
    return {
        "user": session["user"], "role": role,
        "csrf_token": session["csrf_token"], "expires_at": session["expires_at"],
    }


def _clear(response):
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="Strict")
    return response


# --- routes and the hook -----------------------------------------------------


def install(app: Flask, auth_dir: str) -> None:
    """Add the three session routes and the `/api/` hook. `auth_dir` empty turns both off."""

    def off():
        return jsonify({"error": f"sign-in is off: no {AUTH_DIR_ENV} is set"}), 503

    def route_guards():
        """A browser Origin, a query, then (for a POST) the body: `(body, answer)`."""
        if request.headers.get("Origin"):
            return None, (jsonify({"error": "no browser origin may call this service"}), 403)
        if request.args:
            return None, (
                jsonify({"error": f"unknown query parameter {sorted(request.args)[0]!r}"}), 400
            )
        if request.method != "POST":
            return None, None
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return None, (jsonify({"error": "the body is not a JSON object"}), 400)
        return body, None

    def failed(reason: str, user: str | None, status: int = 401, error: str = _FAILED):
        audit(auth_dir, "sign_in_failed", user, reason=reason)
        return jsonify({"error": error}), status

    @app.post("/api/session", endpoint="session_create")
    def session_create():
        if not auth_dir:
            return off()
        body, refused = route_guards()
        if refused is not None:
            return refused
        name, password, code = body.get("name"), body.get("password"), body.get("code")
        if set(body) != {"name", "password", "code"} or not all(
            isinstance(v, str) for v in (name, password, code)
        ):
            return jsonify({"error": "the body is {name, password, code}, all strings"}), 400
        password = password if len(password) <= PASSWORD_MAX else ""
        now = _now()
        with _locked(auth_dir):
            users = _read(_users_path(auth_dir))
            user = users.get(name) if NAME_RE.fullmatch(name) else None
            if user is None:
                verify_password(password, _dummy())
                return failed("unknown_user", None)
            locked = user.get("locked_until")
            if locked and now < _parse(locked):
                audit(auth_dir, "sign_in_failed", name, reason="locked")
                return jsonify(
                    {"error": f"too many failed sign-ins; try again after {locked}"}
                ), 429
            if not verify_password(password, user["password"]):
                reason = "wrong_password"
            else:
                step = _match_step(user["totp_secret"], code, now) if code.isdigit() else None
                if step is None:
                    reason = "wrong_code"
                elif step <= user["totp_last_step"]:
                    reason = "replayed_code"
                else:
                    reason = None
            if reason is not None:
                user["failures"] = user.get("failures", 0) + 1
                if user["failures"] >= MAX_FAILURES:
                    user["failures"] = 0
                    user["locked_until"] = _iso(now + dt.timedelta(seconds=LOCK_SECONDS))
                _write(_users_path(auth_dir), users)
                return failed(reason, name)
            user["totp_last_step"] = step
            user["failures"] = 0
            user["locked_until"] = None
            _write(_users_path(auth_dir), users)
            sid = secrets.token_urlsafe(32)
            session = {
                "user": name,
                "csrf_token": secrets.token_urlsafe(32),
                "created_at": _iso(now),
                "last_seen": _iso(now),
                "expires_at": _iso(now + dt.timedelta(seconds=SESSION_LIFETIME)),
            }
            sessions = _prune(_read(_sessions_path(auth_dir)), now)
            sessions[_sid_key(sid)] = session
            _write(_sessions_path(auth_dir), sessions)
        audit(auth_dir, "sign_in", name)
        response = jsonify(_view(session, user["role"]))
        response.set_cookie(
            COOKIE, sid, max_age=SESSION_LIFETIME, path="/",
            secure=True, httponly=True, samesite="Strict",
        )
        return response

    @app.get("/api/session", endpoint="session_read")
    def session_read():
        if not auth_dir:
            return off()
        _, refused = route_guards()
        if refused is not None:
            return refused
        if not hasattr(g, "session"):
            return jsonify({"error": _NO_SESSION}), 401
        return jsonify(_view(g.session, g.role))

    @app.delete("/api/session", endpoint="session_delete")
    def session_delete():
        if not auth_dir:
            return off()
        _, refused = route_guards()
        if refused is not None:
            return refused
        if not hasattr(g, "session"):
            return jsonify({"error": _NO_SESSION}), 401
        with _locked(auth_dir):
            sessions = _read(_sessions_path(auth_dir))
            sessions.pop(g.session_key, None)
            _write(_sessions_path(auth_dir), _prune(sessions, _now()))
        audit(auth_dir, "sign_out", g.session["user"])
        return _clear(app.response_class("", 204))

    @app.before_request
    def check_session():
        if not auth_dir or not request.path.startswith("/api/"):
            return None
        if request.endpoint == "session_create":
            return None
        sid = request.cookies.get(COOKIE)
        if not sid:
            return None
        now = _now()
        key = _sid_key(sid)
        with _locked(auth_dir):
            sessions = _read(_sessions_path(auth_dir))
            session = sessions.get(key)
            users = _read(_users_path(auth_dir))
            if session is not None and _live(session, now) and session["user"] in users:
                session["last_seen"] = _iso(now)
                _write(_sessions_path(auth_dir), _prune(sessions, now))
                state = None
            else:
                reason = "unknown" if session is None else "expired"
                owner = session.get("user") if session else None
                if session is not None:
                    sessions.pop(key)
                    _write(_sessions_path(auth_dir), _prune(sessions, now))
                state = (reason, owner if owner in users else None)
        if state is not None:
            audit(auth_dir, "session_refused", state[1], reason=state[0])
            return _clear(jsonify({"error": _NO_SESSION})), 401
        role = users[session["user"]]["role"]
        g.session, g.session_key, g.role = session, key, role
        name = session["user"]
        if request.method not in WRITE_METHODS:
            return None
        where = {"method": request.method, "path": request.path}
        presented = request.headers.get(CSRF_HEADER, "")
        if not presented:
            audit(auth_dir, "csrf_refused", name, reason="missing", **where)
            return jsonify({"error": "a CSRF token is required"}), 403
        if not hmac.compare_digest(presented.encode(), session["csrf_token"].encode()):
            audit(auth_dir, "csrf_refused", name, reason="mismatch", **where)
            return jsonify({"error": "a CSRF token is required"}), 403
        if role != "owner" and request.endpoint not in REVIEWER_WRITES:
            audit(auth_dir, "role_refused", name, **where)
            return jsonify({"error": "this needs the owner's role"}), 403
        if request.endpoint != "session_delete":
            audit(auth_dir, "action", name, **where)
        return None
