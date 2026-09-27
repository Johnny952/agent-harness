"""`observability/auth.py` on its own, against an app the test builds itself.

Seven of these arrived from `tests/observability/test_dashboard.py`, which
T-010 deleted with the dashboard: they were only ever about the shared auth and
drove that app because it was the one that had it. They are rehomed rather than
dropped because they were `auth.py`'s only coverage anywhere —
`docs/plans/board.md` says they "moved to the shared `observability/auth.py` in
Phase 1", and that is the one sentence in the plan that is wrong about the
present. Beside them, the token path this phase added (`docs/decisions.md` ADR
7).

The app is two lines of Flask with no service behind it, so nothing here breaks
when a service moves: `auth.py` knows about `request` and `Response` and
nothing else, and that is exactly the surface under test.
"""

import base64

import pytest
from flask import Flask

from observability import auth

# sha256("password")
PASSWORD_HASH = "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8"
REALM = "ia-harness test"
TOKEN = "3f2a9c" * 5


def _client(password_hash: str = PASSWORD_HASH, token: str | None = None, realm: str = REALM):
    app = Flask(__name__)

    @app.get("/")
    @auth.requires_auth("admin", password_hash, realm=realm, token=token)
    def index():
        return "ok"

    return app.test_client()


def _basic(username: str, password: str) -> dict:
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- the human's path, rehomed from test_dashboard.py ---------------------


def test_a_view_with_no_credentials_is_401() -> None:
    assert _client().get("/").status_code == 401


def test_the_right_password_under_the_wrong_username_is_401() -> None:
    resp = _client().get("/", headers=_basic("not-admin", "password"))

    assert resp.status_code == 401


def test_a_non_ascii_username_is_401_not_500() -> None:
    resp = _client().get("/", headers=_basic("josé", "password"))

    assert resp.status_code == 401


def test_a_bearer_header_where_no_token_is_configured_is_401_not_500() -> None:
    """Both halves of this matter. A Bearer header parses into an Authorization
    object whose `.username` and `.password` are None, which is what the
    `auth.type != "basic"` guard exists for; and a service configured with no
    token must reject every bearer rather than degrade into accepting any."""
    resp = _client().get("/", headers=_bearer("abc"))

    assert resp.status_code == 401


def test_a_digest_header_is_401_not_500() -> None:
    resp = _client().get("/", headers={"Authorization": 'Digest username="admin", realm="x"'})

    assert resp.status_code == 401


def test_a_non_ascii_password_hash_is_401_not_500() -> None:
    # A misconfigured DASHBOARD_PASSWORD_HASH shouldn't 500 every login;
    # compare_digest needs bytes on both sides to tolerate that.
    resp = _client(password_hash="not-ascii-é").get("/", headers=_basic("admin", "password"))

    assert resp.status_code == 401


def test_the_401_names_the_realm_it_was_given() -> None:
    """`realm` has no default: it is the one string the services do not share,
    so each one's `WWW-Authenticate` header is its own and pinned by its own
    test. This pins that whatever it is given comes back verbatim."""
    resp = _client(realm="ia-harness something").get("/")

    assert resp.headers["WWW-Authenticate"] == 'Basic realm="ia-harness something"'
    assert resp.data == b"Authentication required"


# --- the service's path ---------------------------------------------------


def test_the_configured_token_as_a_bearer_is_accepted() -> None:
    resp = _client(token=TOKEN).get("/", headers=_bearer(TOKEN))

    assert resp.status_code == 200
    assert resp.data == b"ok"


def test_a_lowercase_bearer_scheme_is_accepted() -> None:
    """The scheme is case-insensitive in the HTTP spec, and a client that sends
    `bearer` is not a client to lock out over a capital letter."""
    resp = _client(token=TOKEN).get("/", headers={"Authorization": f"bearer {TOKEN}"})

    assert resp.status_code == 200


def test_a_wrong_token_is_401() -> None:
    resp = _client(token=TOKEN).get("/", headers=_bearer("nope"))

    assert resp.status_code == 401


def test_a_token_that_is_a_prefix_of_the_configured_one_is_401() -> None:
    resp = _client(token=TOKEN).get("/", headers=_bearer(TOKEN[:-1]))

    assert resp.status_code == 401


@pytest.mark.parametrize("configured", [None, ""])
def test_no_token_configured_accepts_no_token(configured) -> None:
    """The upgrade case: a host whose `docker/compose/.env` predates `API_TOKEN`
    passes nothing through, so the api starts with `token=None`. The bearer path
    is closed there, not open — `compare_digest("", "")` is True, and a service
    that reached it would accept an empty bearer from anyone."""
    client = _client(token=configured)

    assert client.get("/", headers=_bearer("anything")).status_code == 401
    assert client.get("/", headers={"Authorization": "Bearer"}).status_code == 401
    assert client.get("/", headers={"Authorization": "Bearer "}).status_code == 401


def test_the_token_is_not_accepted_in_place_of_the_password() -> None:
    """It is a bearer token and not a second password: sent as the Basic
    password it is compared against the sha256 hash like any other string, and
    it is not that hash."""
    resp = _client(token=TOKEN).get("/", headers=_basic("admin", TOKEN))

    assert resp.status_code == 401


def test_basic_auth_still_works_while_a_token_is_configured() -> None:
    resp = _client(token=TOKEN).get("/", headers=_basic("admin", "password"))

    assert resp.status_code == 200


def test_a_non_ascii_token_on_either_side_is_401_not_500() -> None:
    assert _client(token="tökén").get("/", headers=_bearer("abc")).status_code == 401
    assert _client(token=TOKEN).get("/", headers=_bearer("tökén")).status_code == 401
    assert _client(token="tökén").get("/", headers=_bearer("tökén")).status_code == 200


def test_the_401_still_names_basic_when_a_bearer_was_rejected() -> None:
    """One realm and one challenge whichever credential failed: the human is the
    only caller who can act on a `WWW-Authenticate`, and a service reading a
    `Bearer` challenge cannot re-prompt anyone."""
    resp = _client(token=TOKEN).get("/", headers=_bearer("nope"))

    assert resp.headers["WWW-Authenticate"] == f'Basic realm="{REALM}"'


def test_check_token_needs_both_halves() -> None:
    assert auth.check_token(TOKEN, TOKEN) is True
    assert auth.check_token(TOKEN, None) is False
    assert auth.check_token(None, TOKEN) is False
    assert auth.check_token("", "") is False
    assert auth.check_token(None, None) is False
