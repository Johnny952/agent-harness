# observability/auth.py
"""The Basic auth the observability services share.

Lifted out of `observability/dashboard/app.py:create_app` when the read API
arrived (`docs/plans/board.md` "Phase 1 — a read API in Python"): the
timing-safe comparison and the `auth.type != "basic"` guard are each one line
that is wrong in an interesting way if it is written twice. Both apps now read
the same credentials out of the same two environment variables and answer the
same 401.

Nothing here knows about Flask routing beyond `request` and `Response`, and
nothing here is a login: there are no sessions, no cookies and no users, only
the one username and one sha256 hash an operator put in the compose file.
"""
from __future__ import annotations

import hashlib
import hmac
from functools import wraps

from flask import Response, request


def check_auth(user: str, password: str, username: str, password_hash: str) -> bool:
    """Do these credentials match the ones this service was configured with?"""
    # hmac.compare_digest on str requires ASCII (a non-ASCII Basic-Auth
    # username would otherwise raise TypeError and turn into a 500), so
    # compare encoded bytes. Both comparisons are computed unconditionally
    # before the `and` so a wrong username doesn't return faster than a
    # wrong password.
    user_ok = hmac.compare_digest(user.encode(), username.encode())
    password_ok = hmac.compare_digest(
        hashlib.sha256(password.encode()).hexdigest().encode(), password_hash.encode()
    )
    return user_ok and password_ok


def requires_auth(username: str, password_hash: str, realm: str):
    """Decorator factory: every view it wraps needs these credentials.

    A factory rather than a bare decorator because the credentials arrive as
    `create_app` arguments, and `realm` has no default on purpose — it is the
    one string the two services do not share, and a default here would move
    the dashboard's `WWW-Authenticate` header the first time somebody added a
    third service.
    """

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            auth = request.authorization
            # Bearer/Digest headers parse into an Authorization object whose
            # .username/.password are None; check auth.type first so
            # check_auth never sees None instead of a str.
            if (
                not auth
                or auth.type != "basic"
                or not check_auth(auth.username, auth.password, username, password_hash)
            ):
                return Response(
                    "Authentication required", 401,
                    {"WWW-Authenticate": f'Basic realm="{realm}"'},
                )
            return f(*args, **kwargs)
        return wrapper

    return decorator
