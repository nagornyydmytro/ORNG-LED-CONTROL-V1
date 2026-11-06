"""Discover and load scenes under config/scenes/."""

from __future__ import annotations

from pathlib import Path

from orng_led.config.io import (
    default_config_dir,
    load_controls,
    load_runtime_state,
    load_show_config_from_scene_dir,
    parse_model,
    save_runtime_state,
    scenes_dir,
)
from orng_led.config.models import ConfigError
from orng_led.scenes.models import (
    RuntimeState,
    SceneBundle,
    SceneControls,
    SceneManifest,
    SceneSummary,
)


def is_safe_scene_id(scene_id: str) -> bool:
    cleaned = str(scene_id).strip()
    if not cleaned:
        return False
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        return False
    return cleaned == Path(cleaned).name


def is_scene_directory(path: Path) -> bool:
    """True when ``path`` looks like a scene root (has app.yaml + profiles/)."""
    return (path / "app.yaml").is_file() and (path / "profiles").is_dir()


class SceneStore:
    """Filesystem-backed scene catalog rooted at ``config_dir``."""

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = Path(config_dir) if config_dir is not None else default_config_dir()

    @property
    def scenes_root(self) -> Path:
        return scenes_dir(self.config_dir)

    def list_scenes(self) -> list[SceneSummary]:
        root = self.scenes_root
        if not root.is_dir():
            return []
        summaries: list[SceneSummary] = []
        seen_ids: dict[str, Path] = {}
        from orng_led.cloud.scene_bundle import is_scene_aux_name

        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            folder_name = child.name
            if is_scene_aux_name(folder_name):
                continue
            if not is_safe_scene_id(folder_name):
                summaries.append(
                    SceneSummary(
                        scene_id=folder_name,
                        name=folder_name,
                        path=str(child),
                        error="unsafe scene folder name",
                    )
                )
                continue
            try:
                manifest = self._load_manifest(child)
                if manifest.scene_id != folder_name:
                    raise ConfigError(
                        f"scene_id {manifest.scene_id!r} must match folder {folder_name!r}"
                    )
                if manifest.scene_id in seen_ids:
                    raise ConfigError(
                        f"Duplicate scene_id {manifest.scene_id!r} "
                        f"(also at {seen_ids[manifest.scene_id]})"
                    )
                seen_ids[manifest.scene_id] = child
                # Validate full load; surface corrupt scenes without aborting the catalog.
                self.load_scene(manifest.scene_id)
                summaries.append(
                    SceneSummary(
                        scene_id=manifest.scene_id,
                        name=manifest.name,
                        path=str(child),
                        error=None,
                    )
                )
            except (ConfigError, OSError, ValueError) as exc:
                summaries.append(
                    SceneSummary(
                        scene_id=folder_name,
                        name=folder_name,
                        path=str(child),
                        error=str(exc),
                    )
                )
        return summaries

    def load_scene(self, scene_id: str) -> SceneBundle:
        if not is_safe_scene_id(scene_id):
            raise ConfigError(f"Invalid scene_id: {scene_id!r}")
        scene_path = (self.scenes_root / scene_id).resolve()
        root_resolved = self.scenes_root.resolve()
        try:
            scene_path.relative_to(root_resolved)
        except ValueError as exc:
            raise ConfigError(f"scene_id escapes scenes directory: {scene_id!r}") from exc
        from orng_led.cloud.scene_bundle import recover_interrupted_swap

        recover_interrupted_swap(scene_path)
        if not scene_path.is_dir():
            raise ConfigError(f"Scene not found: {scene_id!r}")
        manifest = self._load_manifest(scene_path)
        if manifest.scene_id != scene_id:
            raise ConfigError(f"scene_id {manifest.scene_id!r} must match folder {scene_id!r}")
        show = load_show_config_from_scene_dir(scene_path)
        controls = load_controls(scene_path / "controls.yaml")
        if controls is None:
            controls = SceneControls()
        return SceneBundle(
            manifest=manifest,
            show=show,
            controls=controls,
            scene_dir=str(scene_path),
        )

    def active_scene_id(self) -> str:
        state = load_runtime_state(self.config_dir)
        return state.active_scene_id

    def load_active_scene(self) -> SceneBundle:
        return self.load_scene(self.active_scene_id())

    def save_active_scene_id(self, scene_id: str) -> RuntimeState:
        if not is_safe_scene_id(scene_id):
            raise ConfigError(f"Invalid scene_id: {scene_id!r}")
        # Ensure the scene exists and loads before persisting.
        self.load_scene(scene_id)
        state = RuntimeState(active_scene_id=scene_id)
        save_runtime_state(self.config_dir, state)
        return state

    def _load_manifest(self, scene_path: Path) -> SceneManifest:
        from orng_led.config.io import read_yaml

        path = scene_path / "scene.yaml"
        if not path.is_file():
            raise ConfigError(f"Missing scene.yaml in {scene_path}")
        return parse_model(SceneManifest, read_yaml(path), source=path)
