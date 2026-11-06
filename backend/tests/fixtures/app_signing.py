"""Test-only Ed25519 private key for APPLICATION release signing.

NEVER import this from production runtime (orng_led.*). Tests and local
``--allow-test-key`` builders may use it.
"""

from __future__ import annotations

import base64
from typing import Any

# PKCS8 DER, matches cloud/tests/helpers/appSigning.ts and TEST_APP_KEY_ID.
TEST_PRIVATE_PKCS8_B64 = "MC4CAQAwBQYDK2VwBCIEIMP0wuUiNAi/QM2mfBQU/BtkAR+UYbKgIuUKfKYnXS0V"
TEST_APP_KEY_ID = "test-ed25519-1"


def sign_with_test_key(manifest: dict[str, Any]) -> str:
    from orng_led.updater.signing import sign_app_manifest_with_private_key

    return sign_app_manifest_with_private_key(
        manifest, base64.b64decode(TEST_PRIVATE_PKCS8_B64)
    )
