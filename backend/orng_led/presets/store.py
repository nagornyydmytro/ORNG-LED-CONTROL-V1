"""Preset registry: load YAML, validate, persist, reload engine programs."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from orng_led.config.models import ConfigError
from orng_led.engine.presets import BasePulsePreset, PresetProgram
from orng_led.presets.io import (
    default_presets_dir,
    load_presets_dir,
    preset_path,
    save_preset,
)
from orng_led.presets.models import EpisodeCard, PresetDocument, PresetSummary
from orng_led.presets.program import YamlPresetProgram


@dataclass
class PresetStore:
    directory: Path = field(default_factory=default_presets_dir)
    documents: dict[str, PresetDocument] = field(default_factory=dict)
    _last_valid: dict[str, PresetDocument] = field(default_factory=dict)

    @classmethod
    def load(cls, directory: Path | None = None) -> PresetStore:
        root = directory or default_presets_dir()
        docs = load_presets_dir(root)
        store = cls(directory=root, documents=docs)
        store._last_valid = {k: copy.deepcopy(v) for k, v in docs.items()}
        return store

    def summaries(self) -> list[PresetSummary]:
        items: list[PresetSummary] = []
        for doc in sorted(self.documents.values(), key=lambda d: d.id):
            items.append(
                PresetSummary(
                    id=doc.id,
                    label=doc.label,
                    builtin=doc.builtin,
                    hardware_tuned=False,
                    episode_count=len(doc.episodes),
                    total_duration_s=doc.total_duration_s,
                    source="yaml",
                    palettes=[ep.palette for ep in doc.episodes],
                    avg_intensity=(
                        sum(ep.intensity for ep in doc.episodes) / len(doc.episodes)
                        if doc.episodes
                        else 0.0
                    ),
                    avg_speed=(
                        sum(ep.speed for ep in doc.episodes) / len(doc.episodes)
                        if doc.episodes
                        else 0.0
                    ),
                    effects=sorted({ep.effect for ep in doc.episodes}),
                )
            )
        if "P05" not in self.documents:
            base = BasePulsePreset()
            items.append(
                PresetSummary(
                    id=base.id,
                    label=base.label,
                    builtin=True,
                    hardware_tuned=False,
                    episode_count=10,
                    total_duration_s=180.0,
                    source="scaffold",
                )
            )
        return sorted(items, key=lambda s: s.id)

    def programs(self) -> dict[str, PresetProgram]:
        programs: dict[str, PresetProgram] = {
            preset_id: YamlPresetProgram(document=doc) for preset_id, doc in self.documents.items()
        }
        if "P05" not in programs:
            programs["P05"] = BasePulsePreset()
        return programs

    def get(self, preset_id: str) -> PresetDocument:
        try:
            return self.documents[preset_id]
        except KeyError as exc:
            raise KeyError(f"Unknown preset {preset_id!r}") from exc

    def create(self, document: PresetDocument) -> PresetDocument:
        if document.id in self.documents:
            raise ConfigError(f"Preset {document.id!r} already exists")
        path = preset_path(self.directory, document.id)
        save_preset(path, document)
        self.documents[document.id] = document
        self._last_valid[document.id] = copy.deepcopy(document)
        return document

    def update(self, preset_id: str, data: dict) -> PresetDocument:
        previous = self._last_valid.get(preset_id) or self.documents.get(preset_id)
        try:
            from pydantic import ValidationError

            document = PresetDocument.model_validate({**data, "id": preset_id})
            document = document.model_copy(update={"hardware_tuned": False})
            if previous is not None and previous.builtin:
                document = document.model_copy(update={"builtin": True})
                if len(document.episodes) != 10:
                    raise ConfigError(f"Builtin preset {preset_id!r} must keep exactly 10 episodes")
                if any(abs(ep.duration_s - 18.0) > 1e-6 for ep in document.episodes):
                    raise ConfigError(
                        f"Builtin preset {preset_id!r} episodes must remain 18 seconds"
                    )
                if abs(document.total_duration_s - 180.0) > 1e-6:
                    raise ConfigError(f"Builtin preset {preset_id!r} must remain 180 seconds total")
            save_preset(preset_path(self.directory, preset_id), document)
            self.documents[preset_id] = document
            self._last_valid[preset_id] = copy.deepcopy(document)
            return document
        except ValidationError as exc:
            if previous is not None:
                self.documents[preset_id] = copy.deepcopy(previous)
            raise ConfigError(f"Invalid preset {preset_id!r}: {exc}") from exc
        except ConfigError:
            if previous is not None:
                self.documents[preset_id] = copy.deepcopy(previous)
            raise

    def rename(self, preset_id: str, new_label: str) -> PresetDocument:
        doc = self.get(preset_id)
        updated = doc.model_copy(update={"label": new_label, "hardware_tuned": False})
        save_preset(preset_path(self.directory, preset_id), updated)
        self.documents[preset_id] = updated
        self._last_valid[preset_id] = copy.deepcopy(updated)
        return updated

    def duplicate(
        self, preset_id: str, new_id: str, new_label: str | None = None
    ) -> PresetDocument:
        source = self.get(preset_id)
        if new_id in self.documents:
            raise ConfigError(f"Preset {new_id!r} already exists")
        clone = source.model_copy(
            update={
                "id": new_id,
                "label": new_label or f"{source.label} (копія)",
                "builtin": False,
                "hardware_tuned": False,
            }
        )
        return self.create(clone)

    def delete(self, preset_id: str) -> None:
        doc = self.get(preset_id)
        if doc.builtin:
            raise ConfigError(f"Builtin preset {preset_id!r} cannot be deleted")
        path = preset_path(self.directory, preset_id)
        if path.exists():
            path.unlink()
        self.documents.pop(preset_id, None)
        self._last_valid.pop(preset_id, None)

    def default_custom_document(self, preset_id: str, label: str) -> PresetDocument:
        episodes = [
            EpisodeCard(
                id=f"ep{index + 1}",
                duration_s=18.0,
                groups=["all_rear"],
                palette="warm_orange" if index % 2 == 0 else "amber",
                effect="pulse" if index % 2 == 0 else "wave",
                speed=0.4 + (index % 5) * 0.1,
                intensity=0.55 + (index % 4) * 0.08,
                transition="soft",
            )
            for index in range(10)
        ]
        return PresetDocument(
            id=preset_id,
            label=label,
            hardware_tuned=False,
            builtin=False,
            episodes=episodes,
        )
