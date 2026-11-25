"""Edge access control: access key, device sessions, throttling.

This module owns the *transport* credential for the HTTP/WebSocket server. It
deliberately knows nothing about sessions, runs, tools or LLM providers — the
gate is applied in ``core.server`` middleware, so nothing below the transport
layer can observe it.

Three pieces of state, each with a different job:

    access key (``boarai_...``)  long-lived, printed once, only its scrypt
                                 hash is ever written to disk
    device session (cookie)      short-lived, signed, revocable per device
    signing secret (0600)        mixed with the key hash to derive the cookie
                                 HMAC key, so rotating the key invalidates
                                 every device without touching the sessions

Why the hash is not enough to forge a session: the HMAC key is
``HMAC(secret, salt || key_hash)``. Reading ``access.yaml`` yields the scrypt
hash (useless without the key) and reading ``access.secret`` yields a random
value that is useless without the hash. Both files are 0600.

Storage layout (data dir anchor, never the source tree):

    <data>/.boarai  → config/access.yaml     key hash, generation, sessions
                    → config/access.secret   signing material (0600)

Timestamps are epoch seconds (float) so comparisons need no parsing; the WebUI
formats them for display.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Human-greppable prefix so a leaked key is recognisable by secret scanners.
KEY_PREFIX = "boarai_"

#: Browser cookie carrying the signed device session.
SESSION_COOKIE = "boarai_session"

_COOKIE_VERSION = "v1"
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

#: Session lifetime defaults (seconds). The WebUI offers a subset of these.
DEFAULT_TTL_SECONDS = 7 * 24 * 3600
MAX_TTL_SECONDS = 30 * 24 * 3600

#: Failed-attempt policy: ``THRESHOLD`` wrong keys inside the window blocks the
#: source, and every failure also imposes an exponential delay (pi-web model).
THROTTLE_FAILURE_THRESHOLD = 10
THROTTLE_WINDOW_SECONDS = 300.0
THROTTLE_BASE_DELAY_SECONDS = 1.0
THROTTLE_MAX_DELAY_SECONDS = 60.0

#: Bound on the persisted device table (oldest evicted first).
MAX_SESSIONS = 50

#: How long a prepared (not yet applied) rotation candidate stays committable.
#: Long enough to copy the key into a password manager, short enough that an
#: abandoned candidate does not linger in memory.
ROTATION_PENDING_SECONDS = 300.0

#: Floor for a key the operator types themselves on the setup page. The
#: browser-generated key is ``boarai_`` + 43 chars, so this only constrains the
#: "use my own key" path. Length alone is not enough — ``a`` * 64 must fail too.
MIN_KEY_LENGTH = 32
MIN_KEY_UNIQUE_CHARS = 12

_CONFIG_DIR = "config"
_ACCESS_FILE = "access.yaml"
_SECRET_FILE = "access.secret"

_ACCESS_STATE_VERSION = 1
_SECRET_VERSION = 1


# ==================== Encoding helpers ====================


def _b64e(raw: bytes) -> str:
    """URL-safe base64 without padding (cookie-safe)."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str) -> Optional[bytes]:
    """Strict URL-safe base64 decode; ``None`` for anything non-canonical."""
    if not value or not re_fullmatch_b64(value):
        return None
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError):
        return None


def re_fullmatch_b64(value: str) -> bool:
    """Whether ``value`` only contains base64url alphabet characters."""
    return all(c.isalnum() or c in "-_" for c in value)


# ==================== Key material ====================


def generate_key() -> str:
    """Create a fresh access key (``boarai_`` + 32 random bytes, base64url)."""
    return KEY_PREFIX + _b64e(secrets.token_bytes(32))


def validate_key_strength(candidate: str) -> Optional[str]:
    """Validate a human-typed key. Returns error message or None."""
    if not candidate:
        return "Key is required"
    if len(candidate) < MIN_KEY_LENGTH:
        return f"Key must be at least {MIN_KEY_LENGTH} characters"
    unique = len(set(candidate))
    if unique < MIN_KEY_UNIQUE_CHARS:
        return f"Key must have at least {MIN_KEY_UNIQUE_CHARS} distinct characters"
    # Reject obvious weak patterns
    if candidate.lower().startswith("my") and candidate.lower().endswith("key"):
        return "Key looks like a placeholder; please use a strong value"
    if re.fullmatch(r"[a-z]+", candidate):
        return "Key must include numbers or symbols"
    return None


def hash_key(key: str) -> str:
    """Return the scrypt hash stored on disk (never the key itself)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        key.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return (
        f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}"
        f"${_b64e(salt)}${_b64e(digest)}"
    )


def verify_key(candidate: str, stored: str) -> bool:
    """Constant-time check of ``candidate`` against a stored scrypt hash."""
    if not candidate or not stored:
        return False
    try:
        scheme, n_s, r_s, p_s, salt_s, digest_s = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = _b64d(salt_s)
        expected = _b64d(digest_s)
        if salt is None or expected is None:
            return False
        actual = hashlib.scrypt(
            candidate.encode("utf-8"),
            salt=salt,
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def mask_key(key: str) -> str:
    """Render a key for logs/UI without revealing it."""
    if not key:
        return ""
    tail = key[-4:] if len(key) >= 8 else ""
    return f"{KEY_PREFIX}…{tail}"


def protect_secret(value: Optional[str]) -> None:
    """Register a plaintext credential with the log redactor."""
    if not value:
        return
    try:
        from boarai.core.logging import register_log_secret

        register_log_secret(value)
    except Exception:  # noqa: BLE001 - protecting a secret must never break its use
        pass


# ==================== Store ====================


class AccessStore:
    """Durable access key + device session table."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: Optional[Dict[str, Any]] = None
        self._secret: Optional[Dict[str, str]] = None
        #: ``(path, mtime)`` the cached state/secret were read at. Re-reading on
        #: change is what makes an out-of-process ``boarai key rotate`` visible
        #: to a running server — and stops the server from writing its stale
        #: copy back over the CLI's rotation.
        self._state_stamp: Tuple[str, float] = ("", -1.0)
        self._secret_stamp: Tuple[str, float] = ("", -1.0)
        #: sha256(candidate) → generation, for keys already proven correct.
        #: scrypt costs ~75 ms and 16 MB; a script authenticating every request
        #: with the same key must not pay that on the event loop each time.
        self._verified: Dict[str, int] = {}
        #: Per-process launch token: whoever can read the server's stdout is
        #: already trusted, so this is a convenience, not a second factor.
        self._launch_token: Optional[str] = None
        #: First-run console code (normalized, no separators). Headless and
        #: remote installs cannot open a browser on the server, so the startup
        #: log prints this once; a non-loopback client must present it to
        #: claim the key. Memory only: a restart re-mints and re-prints it.
        self._setup_code: Optional[str] = None
        #: Prepared-but-unapplied rotation: ``{"key", "expires_at"}``. Memory
        #: only, never persisted — an abandoned candidate must leave no trace
        #: and must not lock anyone out.
        self._pending_rotation: Optional[Dict[str, Any]] = None
        #: ``ip`` → ``{"count", "last_failure", "blocked_until"}``
        self._failures: Dict[str, Dict[str, float]] = {}
        self._global_failures: Dict[str, float] = {
            "count": 0.0,
            "last_failure": 0.0,
            "blocked_until": 0.0,
        }
        #: One-shot WebSocket tickets: ``ticket`` → expiry epoch. The browser
        #: cannot set an Authorization header on an upgrade, and putting the
        #: long-lived key in the URL would leak it into logs/history, so the
        #: page trades its cookie for a 30-second ticket first.
        self._tickets: Dict[str, float] = {}

    # ---- paths ----

    @property
    def config_dir(self) -> Path:
        from boarai.core.runtime import get_data_dir

        return get_data_dir() / _CONFIG_DIR

    @property
    def access_path(self) -> Path:
        return self.config_dir / _ACCESS_FILE

    @property
    def secret_path(self) -> Path:
        return self.config_dir / _SECRET_FILE

    # ---- persistence ----

    def _default_state(self) -> Dict[str, Any]:
        return {
            "version": _ACCESS_STATE_VERSION,
            "generation": 1,
            "key_hash": "",
            "source": "generated",
            "created_at": 0.0,
            "rotated_at": 0.0,
            "sessions": [],
        }

    @staticmethod
    def _stamp(path: Path) -> Tuple[str, float]:
        """``(path, mtime)`` identity used to detect an external edit."""
        try:
            return (str(path), path.stat().st_mtime)
        except OSError:
            return (str(path), 0.0)

    def _load(self) -> Dict[str, Any]:
        path = self.access_path
        stamp = self._stamp(path)
        if self._state is not None and stamp == self._state_stamp:
            return self._state
        if self._state is not None and not stamp[1]:
            # The file vanished under us. Keep serving with the state we have:
            # falling back to defaults here would silently open the gate.
            return self._state
        state = self._default_state()
        try:
            if stamp[1]:
                import yaml

                raw = yaml.safe_load(path.read_text("utf-8")) or {}
                if isinstance(raw, dict):
                    state.update(
                        {k: v for k, v in raw.items() if k in state}
                    )
        except Exception as exc:  # noqa: BLE001 - a broken file must not brick startup
            logger.warning("Could not read %s: %s", path, exc)
        if not isinstance(state.get("sessions"), list):
            state["sessions"] = []
        if self._state is not None and stamp != self._state_stamp:
            # Someone else changed the key/generation: drop proven-key memo.
            self._verified.clear()
        self._state = state
        self._state_stamp = stamp
        return state

    def _save(self) -> None:
        state = self._state or self._default_state()
        try:
            import yaml

            from boarai.core.runtime import write_text_atomic

            self.config_dir.mkdir(parents=True, exist_ok=True)
            payload = yaml.safe_dump(
                state, default_flow_style=False, allow_unicode=True, sort_keys=False
            )
            write_text_atomic(self.access_path, payload)
            self._restrict_permissions(self.access_path)
            # Adopt our own write so the next _load() does not re-parse it.
            self._state_stamp = self._stamp(self.access_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to persist access state: %s", exc)

    @staticmethod
    def _restrict_permissions(path: Path) -> None:
        """Best-effort 0600; Windows has no POSIX mode bits (ACL-scoped)."""
        try:
            os.chmod(path, 0o600)
        except (OSError, NotImplementedError):
            pass

    def _load_secret(self) -> Dict[str, str]:
        path = self.secret_path
        stamp = self._stamp(path)
        if self._secret is not None and stamp == self._secret_stamp:
            return self._secret
        if self._secret is not None and not stamp[1]:
            return self._secret
        try:
            if stamp[1]:
                raw = json.loads(path.read_text("utf-8"))
                if (
                    isinstance(raw, dict)
                    and isinstance(raw.get("salt"), str)
                    and isinstance(raw.get("key"), str)
                ):
                    self._secret = {"salt": raw["salt"], "key": raw["key"]}
                    self._secret_stamp = stamp
                    return self._secret
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read %s: %s", path, exc)

        secret = {
            "version": str(_SECRET_VERSION),
            "salt": _b64e(secrets.token_bytes(32)),
            "key": _b64e(secrets.token_bytes(32)),
        }
        try:
            from boarai.core.runtime import write_text_atomic

            self.config_dir.mkdir(parents=True, exist_ok=True)
            write_text_atomic(path, json.dumps(secret, indent=2) + "\n")
            self._restrict_permissions(path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to persist access secret: %s", exc)
        self._secret = secret
        self._secret_stamp = self._stamp(path)
        return secret

    # ---- key lifecycle ----

    @property
    def configured(self) -> bool:
        """Whether a key hash exists (i.e. the gate can authenticate anyone)."""
        return bool(self._load().get("key_hash"))

    @property
    def source(self) -> str:
        return str(self._load().get("source") or "generated")

    @property
    def generation(self) -> int:
        try:
            return int(self._load().get("generation") or 1)
        except (TypeError, ValueError):
            return 1

    def ensure_key(self, env_key: Optional[str] = None) -> Tuple[Optional[str], str]:
        """Adopt an operator-supplied env key; **never invent one**."""
        with self._lock:
            state = self._load()
            if state.get("key_hash"):
                return None, str(state.get("source") or "generated")
            
            # Materialise the signing secret with the key so an operator sees
            # both files appear together (and neither is ever written later
            # by a request thread while they are looking).
            self._load_secret()

            now = time.time()
            if env_key:
                protect_secret(env_key)
                state["key_hash"] = hash_key(env_key)
                state["source"] = "env"
                state["created_at"] = now
                self._save()
                self.clear_setup_code()
                logger.info("Adopted BOARAI_API_TOKEN as the WebUI access key")
                return None, "env"

            # No env key and no stored key → do NOT generate. Return "unset".
            # The browser setup page will create the key via claim_key().
            return None, "unset"

    def claim_key(
        self,
        candidate: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        *,
        remember: bool = True,
        user_agent: str = "",
        ip: str = "",
    ) -> Tuple[str, Dict[str, Any]]:
        """Claim an access key from the browser setup page."""
        error = validate_key_strength(candidate)
        if error:
            raise ValueError(error)

        with self._lock:
            state = self._load()
            if state.get("key_hash"):
                raise RuntimeError("Access key already configured")

            protect_secret(candidate)
            state["key_hash"] = hash_key(candidate)
            state["source"] = "generated"
            state["created_at"] = time.time()
            self._save()
            self.clear_setup_code()

            # Mint a session for this claiming device
            return self.create_session(ttl_seconds, remember=remember, user_agent=user_agent, ip=ip)

    def generate_and_store(self) -> str:
        """Explicit CLI init: generate + store + return the plaintext once."""
        with self._lock:
            state = self._load()
            if state.get("key_hash"):
                raise RuntimeError("Access key already configured")
            self._load_secret()

            key = generate_key()
            protect_secret(key)
            state["key_hash"] = hash_key(key)
            state["source"] = "generated"
            state["created_at"] = time.time()
            self._save()
            self.clear_setup_code()
            logger.info("Generated a new WebUI access key (%s)", mask_key(key))
            return key

    def _apply_new_key(self, key: str) -> int:
        """Store ``key``'s hash, bump the generation, drop every session."""
        state = self._load()
        protect_secret(key)
        state["key_hash"] = hash_key(key)
        state["source"] = "generated"
        state["generation"] = self.generation + 1
        state["rotated_at"] = time.time()
        state["sessions"] = []
        self._save()
        self._verified.clear()
        # The console hand-off URL is a credential too: "every device out"
        # has to include it, or the old link would immediately mint a new
        # session against the rotated key.
        self._launch_token = None
        logger.info("Access key rotated (generation=%s)", state["generation"])
        return int(state["generation"])

    def rotate(self) -> str:
        """Replace the key immediately and revoke every device session."""
        with self._lock:
            key = generate_key()
            self._apply_new_key(key)
            self._pending_rotation = None
            return key

    def prepare_rotation(self) -> Tuple[str, float]:
        """Generate a candidate key WITHOUT applying it."""
        with self._lock:
            key = generate_key()
            expires_at = time.time() + ROTATION_PENDING_SECONDS
            self._pending_rotation = {"key": key, "expires_at": expires_at}
            return key, expires_at

    def commit_rotation(self) -> Tuple[str, int]:
        """Apply the prepared candidate. Raises when there is nothing pending."""
        with self._lock:
            pending = self._pending_rotation
            self._pending_rotation = None
            if not pending:
                raise RuntimeError("No pending key rotation")
            if float(pending["expires_at"]) < time.time():
                raise RuntimeError("Pending key rotation expired")
            key = str(pending["key"])
            generation = self._apply_new_key(key)
            return key, generation

    def cancel_rotation(self) -> bool:
        """Discard a prepared candidate. True when one was pending."""
        with self._lock:
            had = self._pending_rotation is not None
            self._pending_rotation = None
            return had

    def check_key(self, candidate: str) -> bool:
        """Verify a presented key against the stored hash."""
        if not candidate:
            return False
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        with self._lock:
            state = self._load()
            stored = str(state.get("key_hash") or "")
            if not stored:
                return False
            generation = self.generation
            if self._verified.get(digest) == generation:
                return True
            if not verify_key(candidate, stored):
                return False
            if len(self._verified) >= 8:
                self._verified.clear()
            self._verified[digest] = generation
            return True

    # ---- launch token ----

    def launch_token(self) -> str:
        """Per-process token that mints a session without typing the key."""
        with self._lock:
            if self._launch_token is None:
                self._launch_token = _b64e(secrets.token_bytes(24))
                # The hand-off URL carries this token, and URLs are exactly
                # what ends up pasted into a warning line.
                protect_secret(self._launch_token)
            return self._launch_token

    def check_launch_token(self, candidate: str) -> bool:
        if not candidate:
            return False
        with self._lock:
            if self._launch_token is None:
                return False
            # compare_digest on *str* raises TypeError for non-ASCII input, and
            # this value comes straight off the wire — always compare bytes.
            return hmac.compare_digest(
                candidate.encode("utf-8"), self._launch_token.encode("utf-8")
            )

    # ---- first-run setup code ----

    def setup_code(self) -> str:
        """Console-printed code proving the caller can read the server log.

        Returned grouped (``XXXX-XXXX-XXXX``) for typing; stored normalized.
        Loopback clients never need it — it exists so a headless or remote
        install can finish first-run setup from another machine.
        """
        with self._lock:
            if self._setup_code is None:
                self._setup_code = secrets.token_hex(6).upper()
            raw = self._setup_code
        return "-".join(raw[i:i + 4] for i in (0, 4, 8))

    def check_setup_code(self, candidate: str) -> bool:
        """Constant-time check; separators, case and spacing are ignored."""
        norm = re.sub(r"[^0-9A-Z]", "", (candidate or "").upper())
        with self._lock:
            expected = self._setup_code
        if not norm or not expected:
            return False
        return hmac.compare_digest(norm.encode("utf-8"), expected.encode("utf-8"))

    def clear_setup_code(self) -> None:
        """Drop the console code once a key exists (claim / init / env)."""
        with self._lock:
            self._setup_code = None

    # ---- session signing ----

    def _signing_key(self) -> bytes:
        state = self._load()
        secret = self._load_secret()
        material = (
            f"boarai-access-v1\0{secret.get('salt', '')}\0"
            f"{secret.get('key', '')}\0{state.get('key_hash', '')}"
        )
        return hashlib.sha256(material.encode("utf-8")).digest()

    def _sign(self, body: str) -> str:
        return _b64e(hmac.new(self._signing_key(), body.encode("utf-8"), hashlib.sha256).digest())

    def _serialize_session(self, generation: int, expires_at: float, session_id: str) -> str:
        body = f"{_COOKIE_VERSION}.{generation}.{int(expires_at * 1000)}.{session_id}"
        return f"{body}.{self._sign(body)}"

    # ---- sessions ----

    def create_session(
        self,
        ttl_seconds: int,
        *,
        remember: bool = True,
        user_agent: str = "",
        ip: str = "",
    ) -> Tuple[str, Dict[str, Any]]:
        """Mint a device session and return its cookie value."""
        ttl = max(60, min(int(ttl_seconds), MAX_TTL_SECONDS))
        now = time.time()
        with self._lock:
            state = self._load()
            session_id = _b64e(secrets.token_bytes(12))
            record = {
                "id": session_id,
                "created_at": now,
                "expires_at": now + ttl,
                "ua": (user_agent or "")[:180],
                "ip": (ip or "")[:64],
                "remember": bool(remember),
            }
            sessions: List[Dict[str, Any]] = [
                item
                for item in state.get("sessions", [])
                if isinstance(item, dict) and float(item.get("expires_at") or 0) > now
            ]
            sessions.append(record)
            if len(sessions) > MAX_SESSIONS:
                # Dead rows are already gone; only now evict live ones, oldest
                # first, so a burst of logins cannot silently drop a device
                # that is still in use before it drops expired clutter.
                sessions.sort(key=lambda item: float(item.get("created_at") or 0))
                sessions = sessions[-MAX_SESSIONS:]
            state["sessions"] = sessions
            self._save()
            return self._serialize_session(self.generation, record["expires_at"], session_id), record

    def verify_session(self, cookie_value: Optional[str]) -> Optional[Dict[str, Any]]:
        """Validate a signed session cookie; ``None`` when unusable."""
        if not cookie_value:
            return None
        parts = cookie_value.split(".")
        if len(parts) != 5:
            return None
        version, generation_s, expires_ms_s, session_id, signature = parts
        if version != _COOKIE_VERSION:
            return None
        try:
            generation = int(generation_s)
            expires_at = int(expires_ms_s) / 1000.0
        except (TypeError, ValueError):
            return None
        body = f"{version}.{generation_s}.{expires_ms_s}.{session_id}"
        now = time.time()
        with self._lock:
            # Bytes, not str: a client-supplied non-ASCII signature would make
            # compare_digest(str, str) raise TypeError and turn a bad cookie
            # into a 500 instead of a 401.
            if not hmac.compare_digest(
                signature.encode("utf-8"), self._sign(body).encode("utf-8")
            ):
                return None
            state = self._load()
            if generation != self.generation:
                # A rotation invalidates every outstanding cookie.
                return None
            if expires_at <= now:
                return None
            for record in state.get("sessions", []):
                if isinstance(record, dict) and record.get("id") == session_id:
                    return dict(record)
        return None

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Live (unexpired) device sessions, newest first."""
        now = time.time()
        with self._lock:
            sessions = [
                dict(item)
                for item in self._load().get("sessions", [])
                if isinstance(item, dict) and float(item.get("expires_at") or 0) > now
            ]
        sessions.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return sessions

    def revoke_session(self, session_id: str) -> bool:
        with self._lock:
            state = self._load()
            sessions = state.get("sessions", [])
            kept = [
                item for item in sessions
                if not (isinstance(item, dict) and item.get("id") == session_id)
            ]
            if len(kept) == len(sessions):
                return False
            state["sessions"] = kept
            self._save()
            return True

    def revoke_all_sessions(self) -> int:
        with self._lock:
            state = self._load()
            count = len(state.get("sessions", []))
            state["sessions"] = []
            self._save()
            return count

    def extend_session(self, session_id: str, ttl_seconds: int) -> Optional[Tuple[str, float]]:
        """Push a session's expiry out (sliding TTL); returns a fresh cookie."""
        ttl = max(60, min(int(ttl_seconds), MAX_TTL_SECONDS))
        with self._lock:
            state = self._load()
            for record in state.get("sessions", []):
                if isinstance(record, dict) and record.get("id") == session_id:
                    expires_at = time.time() + ttl
                    record["expires_at"] = expires_at
                    self._save()
                    return (
                        self._serialize_session(self.generation, expires_at, session_id),
                        expires_at,
                    )
        return None

    # ---- WebSocket tickets ----

    def issue_ticket(self, ttl_seconds: int = 30) -> Tuple[str, float]:
        """Mint a single-use upgrade ticket valid for ``ttl_seconds``."""
        expires_at = time.time() + max(5, int(ttl_seconds))
        ticket = _b64e(secrets.token_bytes(24))
        with self._lock:
            self._prune_tickets()
            self._tickets[ticket] = expires_at
        return ticket, expires_at

    def consume_ticket(self, ticket: str) -> bool:
        """Burn ``ticket``; ``True`` only for an unexpired, unused one."""
        if not ticket:
            return False
        with self._lock:
            self._prune_tickets()
            expires_at = self._tickets.pop(ticket, None)
        return expires_at is not None and expires_at > time.time()

    def _prune_tickets(self) -> None:
        now = time.time()
        for ticket in [t for t, exp in self._tickets.items() if exp <= now]:
            self._tickets.pop(ticket, None)

    # ---- throttling ----

    def retry_after(self, ip: str) -> int:
        """Seconds the caller must wait before another attempt (0 = allowed)."""
        now = time.time()
        with self._lock:
            self._expire_failures(now)
            entry = self._failures.get(ip or "")
            ip_wait = 0.0
            if entry:
                ip_wait = max(0.0, entry.get("blocked_until", 0.0) - now)
            global_wait = max(
                0.0, self._global_failures.get("blocked_until", 0.0) - now
            )
        return int(max(ip_wait, global_wait) + 0.999)

    def record_failure(self, ip: str, *, shared: bool = True) -> int:
        """Count one wrong key and return the imposed delay in seconds.

        ``shared=False`` keeps the failure out of the GLOBAL backoff. Used for
        first-run setup-code guesses: that code is 48 random bits, so per-IP
        throttling already defeats guessing, and a stranger must not be able
        to keep the owner's remote claim in a permanent global lockout.
        Wrong access keys stay shared — they protect a human-chosen secret.
        """
        now = time.time()
        with self._lock:
            self._expire_failures(now)
            key = ip or ""
            entry = self._failures.setdefault(
                key, {"count": 0.0, "last_failure": 0.0, "blocked_until": 0.0}
            )
            entry["count"] += 1
            entry["last_failure"] = now
            delay = self._backoff(entry["count"])
            entry["blocked_until"] = now + delay

            global_delay = 0.0
            if shared:
                self._global_failures["count"] += 1
                self._global_failures["last_failure"] = now
                global_delay = self._backoff(self._global_failures["count"])
                self._global_failures["blocked_until"] = now + global_delay

            count = int(entry["count"])
        logger.warning(
            "Access key rejected (attempt %s, %ss delay) from %s", count, delay, ip or "?"
        )
        return int(max(delay, global_delay) + 0.999)

    def record_success(self, ip: str) -> None:
        """Clear this source's counter — deliberately NOT the global one."""
        with self._lock:
            self._failures.pop(ip or "", None)

    @staticmethod
    def _backoff(count: float) -> float:
        """Exponential delay: 1s, 2s, 4s … capped, and a hard block at the threshold."""
        if count <= 0:
            return 0.0
        exponent = min(int(count) - 1, 16)
        delay = THROTTLE_BASE_DELAY_SECONDS * (2 ** exponent)
        delay = min(delay, THROTTLE_MAX_DELAY_SECONDS)
        if count >= THROTTLE_FAILURE_THRESHOLD:
            delay = max(delay, THROTTLE_WINDOW_SECONDS)
            delay = min(delay, THROTTLE_WINDOW_SECONDS)
        return delay

    def _expire_failures(self, now: float) -> None:
        """Forget entries whose window elapsed (bounds memory)."""
        stale = [
            ip
            for ip, entry in self._failures.items()
            if now - entry.get("last_failure", 0.0) >= THROTTLE_WINDOW_SECONDS
        ]
        for ip in stale:
            self._failures.pop(ip, None)
        if (
            self._global_failures.get("count", 0.0) > 0
            and now - self._global_failures.get("last_failure", 0.0)
            >= THROTTLE_WINDOW_SECONDS
        ):
            self._global_failures.update(
                {"count": 0.0, "last_failure": 0.0, "blocked_until": 0.0}
            )


_access_store: Optional[AccessStore] = None


def get_access_store() -> AccessStore:
    """Process-wide access store (lazy; honours the data dir anchor)."""
    global _access_store
    if _access_store is None:
        _access_store = AccessStore()
    return _access_store


def reset_access_store() -> None:
    """Drop the singleton (tests, and after a data-dir change)."""
    global _access_store
    _access_store = None
    from boarai.core.security.gate import reset_access_gate

    reset_access_gate()


__all__ = [
    "AccessStore",
    "DEFAULT_TTL_SECONDS",
    "KEY_PREFIX",
    "MAX_SESSIONS",
    "MAX_TTL_SECONDS",
    "MIN_KEY_LENGTH",
    "MIN_KEY_UNIQUE_CHARS",
    "ROTATION_PENDING_SECONDS",
    "SESSION_COOKIE",
    "THROTTLE_FAILURE_THRESHOLD",
    "THROTTLE_WINDOW_SECONDS",
    "generate_key",
    "get_access_store",
    "hash_key",
    "mask_key",
    "reset_access_store",
    "validate_key_strength",
    "verify_key",
]
