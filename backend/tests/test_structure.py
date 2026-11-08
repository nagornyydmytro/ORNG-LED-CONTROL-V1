"""Corpus first, then synthetic edges. Reasons stay off the desk status."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from orng_led.audio.auto_show import (
    NEAR_SILENCE_S,
    SectionTracker,
    analyze_slice,
    build_auto_stage,
    extra_layer,
)
from orng_led.audio.bpm import click_track
from orng_led.audio.planner import EpisodePlanner
from orng_led.audio.service import BpmService
from orng_led.config.models import FixtureKind
from orng_led.show_bench.corpus import RATE, build_program, even_house

HOP = 0.5


def _tracker_over(samples, *, beat: float | None = 0.5) -> list[dict[str, object]]:
    tracker = SectionTracker()
    tracker.begin_loudness_anchor()
    hop = int(RATE * HOP)
    rows: list[dict[str, object]] = []
    moment = 0.0
    block = samples if hasattr(samples, "tolist") else samples
    size = len(block)
    for start in range(0, size - hop + 1, hop):
        piece = block[start : start + hop]
        hearing = analyze_slice(piece, RATE)
        tracker.push(
            hearing.rms,
            HOP,
            onset=hearing.onset,
            hearing=hearing,
            beat_period_s=beat,
        )
        moment += HOP
        rows.append(
            {
                "t": round(moment, 3),
                "section": tracker.section,
                "hearing": tracker.hearing,
                "level": round(tracker.level, 3),
                "lost": tracker.input_lost,
                "extra": tracker.extra_pulse,
            }
        )
    rows.append({"reasons": list(tracker.reasons), "journal": list(tracker.journal)})
    return rows


def test_corpus_states_keep_their_causes(tmp_path: Path) -> None:
    samples, meta = build_program(RATE)
    root = Path(tempfile.mkdtemp())
    service = BpmService(root, "room")
    service.configure(enabled=True, scope="auto", character="rhythmic")
    hop = int(0.5 * RATE)
    clock = 0.0
    trace: list[dict[str, object]] = []
    while int(clock * RATE) + hop <= samples.size:
        end = clock + 0.5
        block = samples[int(clock * RATE) : int(end * RATE)]
        service.submit_block(block.tolist(), RATE, captured_at=end, now=end)
        section = service.section
        trace.append(
            {
                "t": round(end, 3),
                "section": section.section,
                "hearing": section.hearing,
                "level": round(section.level, 3),
                "lost": section.input_lost,
                "locked": bool(service.follower.locked),
                "bpm": None if service.follower.bpm is None else round(float(service.follower.bpm), 1),
            }
        )
        clock = end
    events = {item["kind"]: item for item in meta["events"] if item["kind"] in {"volume_dip", "silence", "build", "drop"}}
    house = [row for row in trace if 6.0 <= float(row["t"]) <= 20.0]
    assert house
    rhythmic = sum(1 for row in house if row["hearing"] == "rhythmic")
    assert rhythmic / len(house) >= 0.8
    assert all(row["section"] == "groove" for row in house)

    dip = events["volume_dip"]
    before = [float(row["level"]) for row in trace if 28.0 <= float(row["t"]) < float(dip["start_s"])]
    during = [float(row["level"]) for row in trace if float(dip["start_s"]) + 0.8 <= float(row["t"]) <= float(dip["end_s"]) - 0.3]
    assert before and during
    assert sum(during) / len(during) < sum(before) / len(before) * 0.7
    assert all(row["section"] != "drop" for row in trace if float(dip["start_s"]) <= float(row["t"]) <= float(dip["end_s"]))

    silence = events["silence"]
    paused = [row for row in trace if float(silence["start_s"]) <= float(row["t"]) <= float(silence["end_s"])]
    assert any(row["hearing"] == "silence" and row["lost"] is False for row in paused)
    reason = next(item for item in service.section.reasons if item[3] == "silence")
    sound_s, decision_s, _kind, detail = reason
    assert detail == "silence"
    assert decision_s - sound_s == pytest.approx(NEAR_SILENCE_S, abs=0.15)
    # The bar before the marked pause is already quiet, so the decision can lead the mark.
    assert 0.3 <= decision_s - float(silence["start_s"]) <= 2.2

    melody = [row for row in trace if 100.0 <= float(row["t"]) <= 112.0]
    assert melody
    assert sum(1 for row in melody if row["hearing"] == "melodic") / len(melody) >= 0.7
    assert all(row["section"] != "drop" for row in melody)

    build = events["build"]
    drop = events["drop"]
    rising = [
        row
        for row in trace
        if float(build["start_s"]) <= float(row["t"]) < float(drop["start_s"])
    ]
    assert any(row["section"] == "riser" for row in rising)
    landed = [
        row
        for row in trace
        if float(drop["start_s"]) <= float(row["t"]) <= float(drop["start_s"]) + 1.5
    ]
    assert any(row["section"] == "drop" for row in landed)
    payoff = [
        row
        for row in trace
        if float(drop["start_s"]) + 1.5 <= float(row["t"]) <= float(drop["end_s"])
    ]
    assert payoff
    assert all(row["section"] != "riser" for row in payoff)

    out = Path(__file__).resolve().parents[2] / "show-bench" / "runs" / "structure"
    out.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"comparison_only": True, **item}, ensure_ascii=False)
        for item in meta["events"]
        if isinstance(item, dict)
    ]
    lines.extend(
        json.dumps(
            {
                "sound_s": sound,
                "decision_s": decision,
                "frame_s": decision,
                "kind": kind,
                "detail": why,
            },
            ensure_ascii=False,
        )
        for sound, decision, kind, why in service.section.reasons
    )
    (out / "reasons.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert "reasons" not in service.status()
    assert "journal" not in service.status()


def test_fader_does_not_flip_hearing_and_a_kick_alone_is_not_a_drop() -> None:
    quiet = [sample * 0.25 for sample in click_track(120, 3.0, RATE)]
    loud = [sample * 0.9 for sample in click_track(120, 3.0, RATE)]
    rows = _tracker_over(quiet + loud)
    body = rows[:-1]
    loud_half = [row for row in body if float(row["t"]) >= 4.5]
    assert loud_half
    assert {row["hearing"] for row in loud_half} == {"rhythmic"}
    assert all(row["section"] != "drop" for row in body)
    again = click_track(120, 3.0, RATE) + [0.0] * int(0.4 * RATE) + click_track(120, 2.0, RATE)
    back = _tracker_over(again)
    assert all(row["section"] != "drop" for row in back[:-1])


def test_hiss_is_silence_without_a_zero_and_ends_the_rise() -> None:
    tracker = SectionTracker()
    for _ in range(8):
        tracker.push(0.008, 0.25)
    assert tracker._noise_floor == pytest.approx(0.008, abs=0.003)
    tracker.section = "riser"
    for _ in range(int(round(NEAR_SILENCE_S / 0.25))):
        tracker.push(0.008, 0.25)
    assert tracker.section != "riser"
    assert tracker.hearing == "silence"
    assert tracker.input_lost is False
    assert tracker.level == 0.0


def test_confirmed_roll_reaches_the_frame_without_waiting_for_a_second_tempo(monkeypatch, tmp_path: Path) -> None:
    from orng_led.audio import service as service_module

    seen = {"pushed": 0}
    real_push = SectionTracker.push
    real_tempo = service_module.inspect_tempo

    def counting(self, *args, **kwargs):
        seen["pushed"] += 1
        return real_push(self, *args, **kwargs)

    monkeypatch.setattr(SectionTracker, "push", counting)

    def tempo(samples, rate):
        assert seen["pushed"] >= 1
        return real_tempo(samples, rate)

    monkeypatch.setattr(service_module, "inspect_tempo", tempo)
    service = BpmService(tmp_path, "room")
    service.configure(enabled=True, scope="auto")
    block = click_track(120, 0.5, RATE)
    service.feed(block, RATE, dt_s=0.5, now=0.5)
    assert seen["pushed"] >= 1

    groove = click_track(120, 3.0, RATE)
    moment = 0.0
    roll = [0.0] * int(3.5 * RATE)
    while moment < 3.5:
        start = int(moment * RATE)
        for offset in range(min(48, len(roll) - start)):
            roll[start + offset] = max(roll[start + offset], 0.9 * (1.0 - offset / 48))
        frac = min(1.0, moment / (3.5 * 0.65))
        moment += 60.0 / (120.0 * max(0.5, 1.0 + (4.0 - 1.0) * frac))
    hit = [0.0] * int(0.45 * RATE)
    for offset in range(len(hit)):
        hit[offset] = 0.98 * (2.718281828 ** (-offset / (0.07 * RATE))) * __import__("math").sin(
            2.0 * 3.14159265 * 55.0 * offset / RATE
        )
    hit_at = (len(groove) + len(roll)) / RATE
    program = groove + roll + hit + click_track(120, 2.0, RATE)
    tracker = SectionTracker()
    hop = int(0.25 * RATE)
    dropped_at = None
    for cursor in range(0, len(program) - hop + 1, hop):
        hearing = analyze_slice(program[cursor : cursor + hop], RATE)
        before = tracker.section
        tracker.push(hearing.rms, 0.25, onset=hearing.onset, hearing=hearing, beat_period_s=0.5)
        if before != "drop" and tracker.section == "drop":
            dropped_at = tracker.clock_s
            break
    assert dropped_at is not None
    reason = next(item for item in tracker.reasons if item[2] == "drop")
    assert reason[0] <= reason[1]
    assert reason[1] - reason[0] <= 0.5
    assert abs(reason[0] - hit_at) <= 0.5
    assert 0.0 <= reason[1] - hit_at <= 1.0
    delay = {
        "hit_s": round(hit_at, 4),
        "sound_s": reason[0],
        "decision_s": reason[1],
        "frame_s": reason[1],
        "decision_delay_s": round(reason[1] - reason[0], 4),
        "frame_delay_s": round(reason[1] - reason[0], 4),
    }
    folder = Path(__file__).resolve().parents[2] / "show-bench" / "runs" / "structure"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "roll.json").write_text(json.dumps(delay), encoding="utf-8")

    from tests.test_auto_show import _Lamp

    lamp = _Lamp("par", FixtureKind.PAR)
    # Same level. The drop frame holds the hit. The groove at mid-beat has moved on.
    opened = build_auto_stage(
        [lamp], "orange", "drop", 0.0, 0.45, drop_age=tracker.drop_age
    )
    groove_frame = build_auto_stage([lamp], "orange", "groove", 0.5, 0.45)
    assert opened.fixtures["par"].intensity > groove_frame.fixtures["par"].intensity


def test_extra_layer_is_not_a_second_bpm_and_accents_do_not_repeat() -> None:
    assert extra_layer((0.0, 0.5, 1.0), (0.0, 0.5, 1.0)) is False
    assert extra_layer((0.0, 0.5, 1.0), (0.25, 0.75)) is True
    house = even_house()[: int(2.0 * RATE)]
    hearing = analyze_slice(house, RATE)
    assert extra_layer(hearing.low_attacks, hearing.high_attacks) is True
    clicks = analyze_slice(click_track(120, 2.0, RATE), RATE)
    assert extra_layer(clicks.low_attacks, clicks.high_attacks) is False

    ear = SectionTracker()
    ear.accent_amount = 1.0
    ear.consider_accent(now=2.0, onset=0.9, level=0.4, phase=0.4, attack_at=2.0, attack=True)
    first = ear.accent_ready
    strength = ear.accent_strength
    ear.consider_accent(now=2.02, onset=0.9, level=0.4, phase=0.4, attack_at=2.03, attack=True)
    assert ear.accent_ready == first
    assert strength > 1.0
    ear.consider_accent(now=2.0, onset=0.9, level=0.4, phase=0.02, attack_at=3.0, attack=True)
    assert ear.accent_ready == first
    ear.consider_accent(now=4.0, onset=0.01, level=0.4, phase=0.4, attack_at=4.0, attack=True)
    assert ear.accent_ready == first
    ear.consider_accent(now=4.0, onset=0.9, level=0.4, phase=0.4, attack_at=3.7, attack=True)
    assert ear.accent_ready == first
    ear.consider_accent(now=4.0, onset=0.9, level=0.4, phase=0.4, attack_at=4.0, attack=False)
    assert ear.accent_ready == first


def test_dark_limits_are_not_lifted_by_the_five_second_floor() -> None:
    planner = EpisodePlanner()
    planner.intensity_cap = 0.0
    dark = planner.advance(1.0, section="groove", level=0.4, theme="orange")
    assert dark.intensity == 0.0
    planner.intensity_cap = 1.0
    held = planner.advance(2.0, section="groove", level=0.4, theme="orange", blackout=True)
    assert held.intensity == 0.0 or held.look == "blackout" or held.end_early
