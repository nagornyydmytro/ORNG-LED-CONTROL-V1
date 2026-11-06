"""Python and Worker production public-key registries must stay in sync."""

from __future__ import annotations

import json
from pathlib import Path

from orng_led.updater.trusted_keys import (
    PROD_APP_KEY_ID,
    REVOKED_APP_KEY_IDS,
    get_trusted_app_key,
)

REPO = Path(__file__).resolve().parents[2]


def test_production_registry_matches_python_and_worker_builtins() -> None:
    registry = json.loads(
        (REPO / "config" / "app-trusted-keys.production.json").read_text(encoding="utf-8")
    )
    assert PROD_APP_KEY_ID in registry
    key = get_trusted_app_key(PROD_APP_KEY_ID)
    assert key is not None
    assert key.public_key_raw_b64 == registry[PROD_APP_KEY_ID]
    assert get_trusted_app_key("prod-20261025-01") is None
    assert "prod-20261025-01" in REVOKED_APP_KEY_IDS

    ts = (REPO / "cloud" / "src" / "app" / "trusted_keys.ts").read_text(encoding="utf-8")
    assert PROD_APP_KEY_ID in ts
    assert registry[PROD_APP_KEY_ID] in ts
    assert "prod-20261025-01" in ts  # listed as revoked
    assert "REVOKED_APP_KEY_IDS" in ts


def test_wrangler_production_trusts_new_key_not_compromised() -> None:
    wrangler = (REPO / "cloud" / "wrangler.jsonc").read_text(encoding="utf-8")
    registry = json.loads(
        (REPO / "config" / "app-trusted-keys.production.json").read_text(encoding="utf-8")
    )
    assert registry[PROD_APP_KEY_ID] in wrangler
    assert "PXtbvn+d2NNiwW6s5IWoQb4lllcpq/aTjEAMQ0WLB80=" not in wrangler
