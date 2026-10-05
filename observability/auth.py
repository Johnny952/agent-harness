# observability/auth.py
"""The auth the observability services share: a human's password, a service's token.

Lifted out of the first service that had it when the read API arrived
(`docs/plans/board.md` "Phase 1 — a read API in Python"): the timing-safe
comparison and the `auth.type != "basic"` guard are each one line that is wrong
in an interesting way if it is written twice. Every service here reads the same
credentials out of the same two environment variables and answers the same 401 —
the api is the only one left since the Flask board was retired, and this stays a
shared module because those two one-liners are why it was lifted out, not the
number of callers.

Two accepted credentials, one realm (`docs/decisions.md` ADR 7). A human sends
Basic auth — the username and the sha256 hash an operator put in the compose
file. A service sends `Authorization: Bearer <token>`, which is what lets the
console's server half call the api without holding the human's plaintext
password: the api
checks a *hash*, so a service authenticating with Basic would need the
plaintext in `.env` beside the hash and make the hash decorative. Which of the
two a service accepts is an argument, not a default: `requires_auth` takes no
token unless it is given one, and a falsy token closes the bearer path rather
than opening it.

Nothing here knows about Flask routing beyond `request` and `Response`, and
nothing here is a login: there are no sessions, no cookies and no users.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
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


def check_token(presented: str | None, token: str | None) -> bool:
    """Is this the token the service was configured with?

    Both halves have to be non-empty. A service with no token configured
    accepts no token — it must not degrade into accepting everything, which is
    what `compare_digest("", "")` returning True would do to a host whose
    `.env` predates `API_TOKEN`. Encoded bytes on both sides for the reason
    `check_auth` uses them: a non-ASCII token is a 401 and not a 500.
    """
    if not presented or not token:
        return False
    return secrets.compare_digest(presented.encode(), token.encode())


def _bearer(header: str) -> str | None:
    """The token out of an `Authorization: Bearer <token>` header, or None.

    Parsed off the raw header rather than read from `request.authorization.token`
    (`docs/decisions.md` ADR 7): the Basic branch below keeps its
    `auth.type != "basic"` guard exactly as it was, and this path does not
    depend on which Werkzeug version populates `.token`.
    """
    scheme, _, value = header.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def requires_auth(username: str, password_hash: str, realm: str, token: str | None = None):
    """Decorator factory: every view it wraps needs one of these credentials.

    A factory rather than a bare decorator because the credentials arrive as
    `create_app` arguments, and `realm` has no default on purpose — it is the
    one string the services do not share, and a default here would move a
    service's `WWW-Authenticate` header the first time somebody added another.

    `token` is last and optional because a caller may have none: the api is the
    only service that passes one, and it passes whatever `API_TOKEN` holds, which
    on a host whose `.env` predates ADR 7 is nothing. A falsy token leaves a
    human's Basic credential as the only way in rather than opening a bearer path
    in every service that comes to import this module.
    """

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # The bearer path first, and off the raw header: a service sends
            # one header and nothing else, so there is nothing to fall back
            # from. A bearer that does not match falls through to the Basic
            # check, which will not match either, and then to the 401 — never
            # to an exception, including on a service with no token at all.
            if check_token(_bearer(request.headers.get("Authorization", "")), token):
                return f(*args, **kwargs)
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
