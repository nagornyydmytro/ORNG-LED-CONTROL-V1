# Configuration layout

## Scenes (writable show data)

Each venue/show lives under `scenes/<scene_id>/`:

| File | Role |
|---|---|
| `scene.yaml` | Manifest: `schema_version`, `scene_id`, `name` |
| `app.yaml` | Transport, **pad presets**, Art-Net target, speeds |
| `patch.yaml` | Fixture addresses / profiles |
| `layout.yaml` | Stage geometry for the simulator |
| `controls.yaml` | Keyboard / input bindings only (no pad_presets) |
| `profiles/` | Fixture channel profiles |
| `presets/` | Preset YAML documents |

The live writable ORNG BAR scene is **`scenes/orng-bar/`**. Do not keep
duplicate writable `app.yaml` / `patch.yaml` / `layout.yaml` / `profiles/` /
`presets/` at the top of `config/` — those trees were retired after the
multi-scene migration. Baseline copies for equivalence tests live in
`backend/tests/fixtures/pre_scene_orng_bar/`.

Pad slot → preset IDs are owned only by `app.yaml` (`pad_presets`). Q–O in
`controls.yaml` select `pad_slot`; the active preset list comes from app config.

### Adding a scene

1. Copy `scenes/orng-bar/` to `scenes/<new-id>/`.
2. Edit `scene.yaml` (`scene_id` must match the folder name).
3. Adjust patch, layout, profiles, presets, `app.yaml`, and `controls.yaml`.
4. Activate via `POST /api/scenes/activate` with `{ "scene_id": "<new-id>" }`
   (or set `active_scene_id` in a local `runtime.yaml` before start).

`scene_id` must be a single path segment (no `..`, `/`, or `\`).

## Runtime-local (device, not part of a scene)

| File | Role |
|---|---|
| `runtime.yaml` | Device-local active scene (`active_scene_id`) — **gitignored** |
| `runtime.example.yaml` | Tracked example / template |
| `location-show/<scene_id>.json` | Local Auto / show prefs for this desk — **gitignored** |
| `bpm-epoch` | Local BPM epoch marker — **gitignored** |
| `instance.json` / `desk-role.json` | Desk identity and role — **gitignored** |
| `desk-operator.secret` | LAN desk operator password hash — **gitignored** |

`runtime.yaml` is **not** inside a scene folder and must not be shipped with
scene releases or overwritten by project updates. If missing, the default
active scene is `orng-bar`. The first successful scene switch creates it
atomically. Cloning a scene does not copy runtime state.

## Keyboard mapping

Project-wide canonical keys (every program / HOME fallback):

`Q W E R T Y U I O` · `P` (NONE) · `Z X C` · `Escape` · `Space` · `Volume±` · `Zoom±`

`P` = NONE stays in software/UI/laptop; the CH57x macropad has no physical
key for it. No `Backspace` / `Delete` / `Enter` / `F`. Scene `controls.yaml`
may remap HID codes (e.g. CH57x firmware) but requested labels stay this set.

Bindings live in each scene’s `controls.yaml` (`KeyboardEvent.code` strings,
actions, press/release/pulse edges, optional `pad_slot` / `preset_id`).
`KEYBOARD_CODE_MAP` in code is the default/fallback. Zoom-up uses browser
`KeyboardEvent.code` **Equal** (capital E — not the lowercase spelling).

## Linux USB keyboard (optional)

On Linux only, set `ORNG_LINUX_KEYBOARD=1` and install the optional extra:

```bash
pip install -e "backend[linux]"
```

Grant the service user access to `/dev/input/event*` (e.g. `input` group) —
**do not run as root**. Missing or unplugged keyboards never stop Art-Net.
Raspberry Pi venue acceptance for the Linux adapter is **not** done yet.

## Examples

`examples/` holds sample snippets (not loaded as the live show). See
`examples/app.yaml.example` for a template shaped like
`scenes/<scene_id>/app.yaml`.
