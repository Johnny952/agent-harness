"""Web Push: the subscriptions, and the notifier that sends each record (ADR 229).

Step 3 of `docs/plans/notifications.md`. The board service stores a browser's
push subscription here (`add`, `remove`) and runs a `Notifier` on a thread of
its own: every few seconds it reads the records `dispatcher/notifications.py`
appended to `notifications.jsonl` past its cursor, and sends each one to every
subscription, over Web Push signed with the VAPID key.

Three files, all in the hive root beside `notifications.jsonl`:

- `push-subscriptions.json`: `{"subscriptions": [{endpoint, keys, at}]}`,
  replaced whole (a temporary file and `os.replace`) under an `flock`, 0600:
  an endpoint and its keys are the browser's, and nothing but this service
  reads them.
- `push-cursor.json`: `{"offset": <bytes>}`, how far the notifier has read.
  Its first run starts at the file's end, so a new notifier never sends the
  history.
- `.push-subscriptions.lock`, the lock, never removed.

The plan's rules that the server can keep are kept here (*Decisions* 6): each
push has a tag per task, thread or account, so a newer one replaces the older
on the device, and records of one kind within `GROUP_WINDOW_SECONDS` become
one push, "3 tasks awaiting review", tagged by the kind. A kind off by default
in `notifications.KINDS` is not pushed: the person's per-kind settings are
step 5, and so are quiet hours. A push carries the record's title, its url,
its kind and id, and the tag (*Decisions* 4); nothing else.

The send is a seam: `Notifier(send=...)`. The real one, `webpush_send`,
imports `pywebpush` on its first call, so a host without the library loads
this module, serves the routes and only turns the push off. Nothing here
raises into its host: a missing key is one log line and no push, a failed send
is a warning, and a subscription the push service answers 404 or 410 is gone
and dropped.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import urllib.parse
from typing import Callable

from dispatcher import context_transfer, notifications

logger = logging.getLogger(__name__)

SUBSCRIPTIONS_FILE = "push-subscriptions.json"
CURSOR_FILE = "push-cursor.json"
LOCK_FILE = ".push-subscriptions.lock"

#: Where the VAPID private key is read from, inside the board service's
#: container: a PEM EC P-256 key. On the host it is
#: `.data/credentials/vapid-private.pem`, mounted read-only, as the git
#: credential is (`git_credentials_path`).
VAPID_KEY_PATH = "/run/secrets/vapid-private.pem"

#: The VAPID `sub` claim: a contact for the push services. Apple's refuses a
#: placeholder domain, so a real deployment sets `push_subject`.
DEFAULT_SUBJECT = "mailto:admin@localhost"

#: How often the notifier reads the file. A push a few seconds late is still
#: the push; the console polls every five.
INTERVAL_SECONDS = 5.0

#: Records of one kind closer than this to the last push of that kind are
#: grouped: "a few minutes" in the plan.
GROUP_WINDOW_SECONDS = 300.0

#: How long a push service keeps a push for a device that is off.
TTL_SECONDS = 24 * 3600

#: A bound on the store, so a caller with the bearer cannot make it unbounded.
MAX_SUBSCRIPTIONS = 50
MAX_ENDPOINT_CHARS = 2048

#: Bytes the notifier reads in one tick; the rest waits for the next.
MAX_READ_BYTES = 1 << 20

#: The grouped push's title, `<n> <label>`, and where it opens.
GROUP_LABELS = {
    "awaiting_review": "tasks awaiting review",
    "conflict": "merge conflicts",
    "command_request": "command requests",
    "blocked": "tasks blocked",
    "merged": "tasks merged",
    "pool": "pool changes",
    "phase_failed": "phases failed",
    "turn_finished": "thread turns finished",
}
GROUP_URL = "/notifications"

#: The browser's `PushSubscription.toJSON()`: these keys and no others.
_SUBSCRIPTION_KEYS = {"endpoint", "keys", "expirationTime"}
#: Decoded lengths: an uncompressed P-256 point, and the 16-byte auth secret.
_KEY_BYTES = {"p256dh": 65, "auth": 16}


class SubscriptionRefused(ValueError):
    """A subscription body that is not one; its message says why."""


class StoreFull(SubscriptionRefused):
    """A new subscription past `MAX_SUBSCRIPTIONS`."""


# -- the store ---------------------------------------------------------------


def _root(hive_dir: str) -> str:
    return context_transfer.hive_root(hive_dir)


def subscriptions_path(hive_dir: str) -> str:
    return os.path.join(_root(hive_dir), SUBSCRIPTIONS_FILE)


def cursor_path(hive_dir: str) -> str:
    return os.path.join(_root(hive_dir), CURSOR_FILE)


def _b64url_bytes(value: object, name: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise SubscriptionRefused(f"keys.{name} must be a base64url string")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError):
        raise SubscriptionRefused(f"keys.{name} is not base64url") from None


def validate(body: object) -> dict:
    """The subscription in `body`, as stored: `{endpoint, keys: {p256dh, auth}}`.

    `body` is what the browser's `PushSubscription.toJSON()` gives; its
    `expirationTime` is accepted and not kept. Raises `SubscriptionRefused`.
    """
    if not isinstance(body, dict):
        raise SubscriptionRefused("the body is not a JSON object")
    unknown = sorted(set(body) - _SUBSCRIPTION_KEYS)
    if unknown:
        raise SubscriptionRefused(f"unknown field {unknown[0]!r}")
    endpoint = endpoint_of(body)
    expiration = body.get("expirationTime")
    if expiration is not None and (
        isinstance(expiration, bool) or not isinstance(expiration, (int, float))
    ):
        raise SubscriptionRefused("expirationTime must be a number or null")
    keys = body.get("keys")
    if not isinstance(keys, dict):
        raise SubscriptionRefused("keys must be an object with p256dh and auth")
    if set(keys) != set(_KEY_BYTES):
        raise SubscriptionRefused("keys must hold p256dh and auth, and nothing else")
    for name, size in _KEY_BYTES.items():
        raw = _b64url_bytes(keys[name], name)
        if len(raw) != size or (name == "p256dh" and raw[0] != 4):
            raise SubscriptionRefused(f"keys.{name} is not a {size}-byte key")
    return {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}


def endpoint_of(body: object) -> str:
    """`body["endpoint"]`, an https:// URL with a host, or `SubscriptionRefused`."""
    endpoint = body.get("endpoint") if isinstance(body, dict) else None
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > MAX_ENDPOINT_CHARS:
        raise SubscriptionRefused("endpoint must be an https:// URL")
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or any(c.isspace() for c in endpoint):
        raise SubscriptionRefused("endpoint must be an https:// URL")
    if parsed.username is not None or parsed.password is not None:
        raise SubscriptionRefused("endpoint must carry no credential")
    return endpoint


@contextlib.contextmanager
def _lock(hive_dir: str):
    """An exclusive `flock` on the store's lock file, for one read-modify-write."""
    root = _root(hive_dir)
    os.makedirs(root, exist_ok=True)
    fd = os.open(os.path.join(root, LOCK_FILE), os.O_RDONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _write_json(path: str, value: dict) -> None:
    """`value` at `path`, whole or not at all: a temporary file and `os.replace`. 0600."""
    fd, staged = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".new", dir=os.path.dirname(path)
    )
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(value, fh, indent=2)
            fh.write("\n")
        os.chmod(staged, 0o600)
        os.replace(staged, path)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)


def read_subscriptions(hive_dir: str) -> list[dict]:
    """The stored subscriptions; none for a missing or unreadable store, logged."""
    path = subscriptions_path(hive_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        logger.warning("push subscriptions at %s do not read: %s", path, exc)
        return []
    rows = data.get("subscriptions") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        logger.warning("push subscriptions at %s have no subscriptions list", path)
        return []
    kept = []
    for row in rows:
        try:
            kept.append({**validate({k: row[k] for k in ("endpoint", "keys")}),
                         "at": row.get("at")})
        except (SubscriptionRefused, KeyError, TypeError):
            logger.warning("a push subscription at %s does not read, skipped", path)
    return kept


def add(hive_dir: str, body: object, *, now: dt.datetime | None = None) -> tuple[dict, bool]:
    """Store the subscription in `body`: `(stored, created)`.

    The same endpoint again replaces its keys and is not created. Past
    `MAX_SUBSCRIPTIONS` a new one is refused.
    """
    record = validate(body)
    record["at"] = (now or dt.datetime.now(dt.timezone.utc)).isoformat()
    with _lock(hive_dir):
        rows = read_subscriptions(hive_dir)
        others = [row for row in rows if row["endpoint"] != record["endpoint"]]
        created = len(others) == len(rows)
        if created and len(rows) >= MAX_SUBSCRIPTIONS:
            raise StoreFull(f"there are already {MAX_SUBSCRIPTIONS} subscriptions")
        _write_json(subscriptions_path(hive_dir), {"subscriptions": [*others, record]})
    return record, created


def remove(hive_dir: str, endpoint: str) -> bool:
    """Drop the subscription at `endpoint`; whether there was one."""
    with _lock(hive_dir):
        rows = read_subscriptions(hive_dir)
        kept = [row for row in rows if row["endpoint"] != endpoint]
        if len(kept) == len(rows):
            return False
        _write_json(subscriptions_path(hive_dir), {"subscriptions": kept})
    return True


# -- the key -----------------------------------------------------------------


def public_key(key_path: str) -> str:
    """The VAPID public key for the browser's `applicationServerKey`: base64url.

    The uncompressed P-256 point of the private key at `key_path`. Raises
    `OSError` for a missing file, `ValueError` for one that is not such a key,
    and `ImportError` when `cryptography` (pywebpush's own dependency) is not
    installed.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    with open(key_path, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError("the VAPID key is not an EC P-256 private key")
    point = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(point).rstrip(b"=").decode("ascii")


# -- the send ----------------------------------------------------------------

#: `send(subscription, payload, *, key_path, subject, ttl, headers)`: the push
#: service's status code, or an exception.
Send = Callable[..., int]


def webpush_send(
    subscription: dict, payload: str, *, key_path: str, subject: str, ttl: int, headers: dict
) -> int:
    """One push through `pywebpush`, imported here so its absence only turns push off."""
    from pywebpush import WebPushException, webpush

    try:
        response = webpush(
            subscription_info={"endpoint": subscription["endpoint"], "keys": subscription["keys"]},
            data=payload,
            vapid_private_key=key_path,
            # A fresh dict per call: pywebpush writes the audience into it.
            vapid_claims={"sub": subject},
            ttl=ttl,
            headers=dict(headers),
        )
    except WebPushException as exc:
        if exc.response is not None:
            return exc.response.status_code
        raise
    return response.status_code


def _topic(tag: str) -> str:
    """The `Topic` header: a push service keeps one pending push per topic. 32 chars."""
    digest = hashlib.sha256(tag.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")[:32]


def subject_tag(record: dict) -> str:
    """`task:<id>`, `thread:<id>` or `account:<id>`: one tag per item (*Decisions* 6)."""
    for key in notifications.SUBJECTS:
        if isinstance(record.get(key), str):
            return f"{key}:{record[key]}"
    return f"kind:{record['kind']}"


# -- the notifier ------------------------------------------------------------


class Notifier:
    """Reads `notifications.jsonl` past its cursor and pushes each record.

    `tick` is one pass and never raises; `run` is the loop the board service
    starts on a daemon thread, `Worker.run`'s shape: a tick, then a wait on
    `stopping`.
    """

    def __init__(
        self,
        hive_dir: str,
        *,
        key_path: str = VAPID_KEY_PATH,
        subject: str = DEFAULT_SUBJECT,
        send: Send | None = None,
        clock: Callable[[], float] = time.monotonic,
        group_window: float = GROUP_WINDOW_SECONDS,
    ) -> None:
        self.hive_dir = hive_dir
        self.key_path = key_path
        self.subject = subject
        self.send = send or webpush_send
        self.clock = clock
        self.group_window = group_window
        #: Per kind, when each record pushed within the window was pushed.
        self._recent: dict[str, list[float]] = {}
        self._off_logged = False

    # The cursor.

    def _read_cursor(self) -> int | None:
        try:
            with open(cursor_path(self.hive_dir), encoding="utf-8") as fh:
                offset = json.load(fh).get("offset")
        except FileNotFoundError:
            return None
        except (OSError, ValueError, AttributeError) as exc:
            logger.warning("push cursor does not read (%s); starting at the end", exc)
            return None
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            logger.warning("push cursor %r is not an offset; starting at the end", offset)
            return None
        return offset

    def _write_cursor(self, offset: int) -> None:
        _write_json(cursor_path(self.hive_dir), {"offset": offset})

    # One pass.

    def enabled(self) -> bool:
        """Whether there is a key to sign with. Logged once when there is not."""
        if os.path.isfile(self.key_path):
            if self._off_logged:
                logger.info("push is on: the VAPID key at %s is there", self.key_path)
            self._off_logged = False
            return True
        if not self._off_logged:
            logger.warning(
                "push is off: no VAPID key at %s (push_vapid_key_path)", self.key_path
            )
            self._off_logged = True
        return False

    def tick(self) -> int:
        """One pass: the pushes sent. Never raises."""
        try:
            return self._tick()
        except Exception:  # noqa: BLE001 - the host's other work outlives a push
            logger.exception("push notifier pass failed")
            return 0

    def _tick(self) -> int:
        if not self.enabled():
            return 0
        records = self._new_records()
        if not records:
            return 0
        subscriptions = read_subscriptions(self.hive_dir)
        sent = 0
        for push in self._pushes(records):
            if not subscriptions:
                break
            sent += self._deliver(push, subscriptions)
        return sent

    def _new_records(self) -> list[dict]:
        """The whole lines past the cursor, parsed; the cursor moved past them."""
        path = notifications.notifications_path(self.hive_dir)
        try:
            size = os.path.getsize(path)
        except FileNotFoundError:
            size = 0
        offset = self._read_cursor()
        if offset is None or offset > size:
            if offset is not None:
                logger.warning(
                    "notifications.jsonl is shorter (%d) than the push cursor (%d); "
                    "starting at its end", size, offset,
                )
            self._write_cursor(size)
            return []
        if offset == size:
            return []
        with open(path, "rb") as fh:
            fh.seek(offset)
            chunk = fh.read(min(size - offset, MAX_READ_BYTES))
        end = chunk.rfind(b"\n")
        if end < 0:
            if len(chunk) >= MAX_READ_BYTES:
                # One line longer than a whole read is not a record: skip it.
                logger.warning("a notifications.jsonl line is over %d bytes, skipped",
                               MAX_READ_BYTES)
                self._write_cursor(offset + len(chunk))
            return []
        records = []
        for line in chunk[: end + 1].splitlines():
            if not line.strip():
                continue
            record, problem = notifications.parse_line(line)
            if problem:
                logger.warning("notifications.jsonl: %s", problem)
                continue
            records.append(record)
        self._write_cursor(offset + end + 1)
        return records

    def _pushes(self, records: list[dict]) -> list[dict]:
        """The pushes `records` make: one each, or one per kind when grouped."""
        now = self.clock()
        by_kind: dict[str, list[dict]] = {}
        for record in records:
            if not notifications.KINDS[record["kind"]][1]:
                continue  # Off by default; the per-kind settings are step 5.
            by_kind.setdefault(record["kind"], []).append(record)
        pushes = []
        for kind, group in by_kind.items():
            recent = [t for t in self._recent.get(kind, []) if now - t < self.group_window]
            recent.extend(now for _ in group)
            self._recent[kind] = recent
            if len(recent) == 1:
                record = group[0]
                pushes.append(self._payload(record, record["title"], record["url"],
                                            subject_tag(record)))
            else:
                title = f"{len(recent)} {GROUP_LABELS.get(kind, kind)}"
                pushes.append(self._payload(group[-1], title, GROUP_URL, f"kind:{kind}"))
        return pushes

    @staticmethod
    def _payload(record: dict, title: str, url: str, tag: str) -> dict:
        return {"id": record["id"], "kind": record["kind"], "title": title, "url": url,
                "tag": tag}

    def _deliver(self, push: dict, subscriptions: list[dict]) -> int:
        """`push` to each subscription; one answered 404 or 410 is dropped."""
        payload = json.dumps(push, ensure_ascii=False, separators=(",", ":"))
        urgent = notifications.KINDS[push["kind"]][0] == notifications.NEEDS_PERSON
        headers = {"Topic": _topic(push["tag"]), "Urgency": "high" if urgent else "normal"}
        sent = 0
        for subscription in list(subscriptions):
            host = urllib.parse.urlsplit(subscription["endpoint"]).hostname
            try:
                status = self.send(
                    subscription, payload, key_path=self.key_path, subject=self.subject,
                    ttl=TTL_SECONDS, headers=headers,
                )
            except Exception as exc:  # noqa: BLE001 - one send never stops the rest
                logger.warning("push %s to %s failed: %s", push["id"], host, exc)
                continue
            if status in (404, 410):
                logger.info("push subscription at %s is gone (%d); dropped", host, status)
                subscriptions.remove(subscription)
                try:
                    remove(self.hive_dir, subscription["endpoint"])
                except OSError as exc:
                    logger.warning("push subscription at %s not dropped: %s", host, exc)
            elif 200 <= status < 300:
                sent += 1
            else:
                logger.warning("push %s to %s answered %d", push["id"], host, status)
        return sent

    def run(self, stopping: threading.Event, interval: float = INTERVAL_SECONDS) -> None:
        while not stopping.is_set():
            self.tick()
            stopping.wait(interval)


def start(notifier: Notifier, interval: float = INTERVAL_SECONDS) -> threading.Event:
    """`notifier.run` on a daemon thread; set the event returned to stop it."""
    stopping = threading.Event()
    threading.Thread(
        target=notifier.run, args=(stopping, interval), name="push-notifier", daemon=True
    ).start()
    return stopping
