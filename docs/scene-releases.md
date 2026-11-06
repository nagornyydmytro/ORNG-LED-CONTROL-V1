# Scene releases

Immutable, content-addressed scene bundles for cloud → device sync.

## Allowed files only

- `scene.yaml`, `app.yaml`, `patch.yaml`, `layout.yaml`, `controls.yaml`
- `profiles/*.yaml`, `presets/*.yaml`

Rejected: `..`, symlinks, `runtime.yaml`, secrets, nested paths beyond one directory, oversize bundles.

## Bundle format (`orng-scene-bundle-v1`)

Deterministic JSON:

```json
{
  "format": "orng-scene-bundle-v1",
  "files": [
    { "path": "app.yaml", "sha256": "...", "data_b64": "..." }
  ]
}
```

Files sorted by path; JSON keys sorted. `content_sha256` = SHA-256 of the canonical JSON bytes.

## Publish

`POST /admin/scenes/:scene_id/publish`

- multipart `bundle` **or** JSON `{ bundle_base64, name, base_version }`
- Optimistic concurrency: `base_version` must match current max version → **409** on conflict
- Same `content_hash` → **idempotent** return of existing version
- R2 key: `scenes/{scene_id}/v{version}/{content_hash}.bundle` (immutable)

## Manifest

```json
{
  "schema": 1,
  "scene_id": "orng-bar",
  "version": 3,
  "content_sha256": "...",
  "files": { "app.yaml": "..." },
  "size_bytes": 12345,
  "min_app_version": null
}
```

## Device apply (safe)

Local agent / `/api/cloud/sync`:

1. Release holds → blackout → dark frames → disarm  
2. Stage bundle to temp dir → validate (`load_show_config_from_scene_dir`)  
3. Atomic swap into `config/scenes/{id}`  
4. `activate_scene` → stay **blackout + disarmed**  
5. On failure: rollback; corrupt bundle never replaces live scene  

Local dirty vs remote hash → `conflict` unless `force`.

## Python helpers

`orng_led.cloud.scene_bundle.pack_scene_bundle` / `unpack_scene_bundle` / `apply_release_safely`.
