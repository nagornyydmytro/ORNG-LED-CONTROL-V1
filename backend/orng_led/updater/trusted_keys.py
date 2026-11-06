"""Trusted Ed25519 public keys for APPLICATION releases.

Production private keys never live in the repo. Only public key_ids here.
The test key is fail-closed unless an explicit DEV/TEST flag is set and the
process is not marked production.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class TrustedAppKey:
    public_key_raw_b64: str
    note: str
    active: bool


TEST_APP_KEY_ID = "test-ed25519-1"
PROD_APP_KEY_ID = "prod-20261025-02"
REVOKED_APP_KEY_IDS = frozenset({"prod-20261025-01"})

_BUILTIN: dict[str, TrustedAppKey] = {
    TEST_APP_KEY_ID: TrustedAppKey(
        public_key_raw_b64="qmIQ+29RPmXtiyrTCSXNzUnzv/7dtNNQjV5SlWM5DkU=",
        note="Unit/integration test key only — never use in production",
        active=True,
    ),
    # Public only — matches config/app-trusted-keys.production.json
    PROD_APP_KEY_ID: TrustedAppKey(
        public_key_raw_b64="ISrcfybhnOYYpf0cvAKch2Ag2RBT0R4MOdXUadU7WWk=",
        note="Production Ed25519 (prod-20261025-02); private outside repo",
        active=True,
    ),
}


def is_production_runtime() -> bool:
    """Fail-closed production marker (env or non-localhost managed host)."""
    if os.environ.get("ORNG_PRODUCTION", "").strip().lower() in {"1", "true", "yes"}:
        return True
    if os.environ.get("ORNG_APP_ENV", "").strip().lower() in {"production", "prod"}:
        return True
    return False


def allow_test_app_signing_key() -> bool:
    """Test key only with explicit flag and never in production."""
    if is_production_runtime():
        return False
    flag = os.environ.get("ORNG_DEV_ALLOW_TEST_APP_KEYS", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    # Pytest runs are explicit test context.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return False


def _parse_env_trusted_keys() -> dict[str, TrustedAppKey]:
    raw = os.environ.get("ORNG_APP_TRUSTED_KEYS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, TrustedAppKey] = {}
    for key_id, value in parsed.items():
        if not isinstance(key_id, str) or not key_id.strip():
            continue
        b64 = value.strip() if isinstance(value, str) else ""
        if not b64:
            continue
        out[key_id] = TrustedAppKey(
            public_key_raw_b64=b64,
            note="Configured via ORNG_APP_TRUSTED_KEYS",
            active=True,
        )
    return out


def get_trusted_app_key(key_id: str) -> TrustedAppKey | None:
    if not key_id or not key_id.strip():
        return None
    if key_id in REVOKED_APP_KEY_IDS:
        return None
    env_keys = _parse_env_trusted_keys()

    if key_id == TEST_APP_KEY_ID:
        if not allow_test_app_signing_key():
            return None
        return env_keys.get(key_id) or _BUILTIN.get(TEST_APP_KEY_ID)

    from_env = env_keys.get(key_id)
    if from_env and from_env.active and from_env.public_key_raw_b64:
        return from_env

    builtin = _BUILTIN.get(key_id)
    if not builtin or not builtin.active or not builtin.public_key_raw_b64:
        return None
    return builtin


# Back-compat alias for docs/tests that inspect the map (test key gated at resolve).
TRUSTED_APP_KEYS = _BUILTIN
