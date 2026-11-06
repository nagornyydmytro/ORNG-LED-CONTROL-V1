"""Worker ↔ device APPLICATION download contract.

Documented contract (Phase 3 Repair):

1. ``GET /device/app/releases/{platform}/{arch}/{version}/{build_id}/manifest``
   JSON:
   ``{manifest, signature_b64, key_id, content_sha256, package_size, package_url}``

2. ``GET …/package`` — raw gzip bytes with
   ``Content-Type: application/gzip``, ``Content-Length``, ``x-content-sha256``.

Legacy bare ``GET …/{build_id}`` is deprecated and must not be used by new clients.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from orng_led.cloud.credentials import load_device_token
from orng_led.cloud.sync_service import cloud_http_base
from orng_led.updater.mode import detect_arch, detect_platform


class AppDownloadError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DownloadedAppRelease:
    package_bytes: bytes
    manifest: dict[str, Any]
    signature_b64: str
    key_id: str
    content_sha256: str
    package_size: int
    platform: str
    arch: str
    version: str
    build_id: str
    channel: str | None


async def download_assigned_app_release(
    *,
    version: str | None = None,
    build_id: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> DownloadedAppRelease:
    """Fetch assignment → manifest → package and validate identity/hash headers."""
    token = load_device_token()
    if not token:
        raise AppDownloadError("no_token", "No device token")
    http_base = cloud_http_base()
    platform = detect_platform()
    arch = detect_arch()
    if not arch or arch == "unknown":
        raise AppDownloadError("unknown_arch", "Device arch is unknown; cannot download")

    owns = client is None
    client = client or httpx.AsyncClient(timeout=120.0)
    try:
        headers = {"authorization": f"Bearer {token}"}
        assign = await client.get(f"{http_base}/device/app/assignment", headers=headers)
        if assign.status_code >= 400:
            raise AppDownloadError("assignment_failed", assign.text[:200])
        assignment = (assign.json() or {}).get("assignment") or {}
        version = version or assignment.get("desired_version")
        build_id = build_id or assignment.get("desired_build_id")
        if not version or not build_id:
            raise AppDownloadError("no_desired", "No desired app release")

        base = (
            f"{http_base}/device/app/releases/"
            f"{platform}/{arch}/{version}/{build_id}"
        )
        man_res = await client.get(f"{base}/manifest", headers=headers)
        if man_res.status_code >= 400:
            raise AppDownloadError("manifest_failed", f"HTTP {man_res.status_code}")
        body = man_res.json()
        manifest = dict(body.get("manifest") or {})
        signature_b64 = str(body.get("signature_b64") or "")
        key_id = str(body.get("key_id") or "")
        content_sha256 = str(body.get("content_sha256") or manifest.get("content_sha256") or "")
        package_size = int(body.get("package_size") or manifest.get("package_size") or 0)
        package_url = str(body.get("package_url") or f"{base}/package")
        if package_url.startswith("/"):
            package_url = http_base.rstrip("/") + package_url

        pkg_res = await client.get(package_url, headers=headers)
        if pkg_res.status_code >= 400:
            raise AppDownloadError("package_failed", f"HTTP {pkg_res.status_code}")
        package = pkg_res.content
        header_hash = pkg_res.headers.get("x-content-sha256") or pkg_res.headers.get(
            "x-content-hash"
        )
        if header_hash and content_sha256 and header_hash != content_sha256:
            raise AppDownloadError("hash_mismatch", "Manifest/package hash disagree")
        if package_size and len(package) != package_size:
            raise AppDownloadError(
                "size_mismatch",
                f"Expected {package_size} bytes, got {len(package)}",
            )

        return DownloadedAppRelease(
            package_bytes=package,
            manifest=manifest,
            signature_b64=signature_b64,
            key_id=key_id,
            content_sha256=content_sha256 or header_hash or "",
            package_size=len(package),
            platform=str(manifest.get("platform") or platform),
            arch=str(manifest.get("arch") or arch),
            version=str(manifest.get("version") or version),
            build_id=str(manifest.get("build_id") or build_id),
            channel=manifest.get("channel"),
        )
    finally:
        if owns:
            await client.aclose()
