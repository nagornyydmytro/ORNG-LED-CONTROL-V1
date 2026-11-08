"""The home bench stays on Mock and does not inject a tempo."""

from __future__ import annotations

import sys
from pathlib import Path

from orng_led.show_bench.corpus import build_program
from orng_led.show_bench.session import run_session

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from export_safe_archive import _excluded  # noqa: E402


def test_corpus_has_six_pieces_a_dip_and_a_pause() -> None:
    samples, manifest = build_program()
    pieces = [event for event in manifest["events"] if event["kind"] == "piece"]
    assert [event["id"] for event in pieces] == [
        "even_house",
        "dense_techno",
        "broken_beat",
        "slow_syncopated",
        "melody",
        "buildup_drop",
    ]
    kinds = {event["kind"] for event in manifest["events"]}
    assert {"silence", "volume_dip", "build", "drop", "crossfade"} <= kinds
    assert manifest["origin"] == "original-synthesis"
    assert manifest["injected_into_engine"] is False
    assert manifest["comparison_only"] is True
    assert float(manifest["duration_s"]) > 90.0
    assert samples.size > 90 * 44100


def test_index_points_at_the_video_beside_the_page(tmp_path: Path) -> None:
    run = tmp_path / "runs" / "sample"
    run.mkdir(parents=True)
    (run / "picture.mp4").write_bytes(b"not-a-video")
    (run / "observations.md").write_text("ok", encoding="utf-8")
    from orng_led.show_bench.session import _index

    _index(tmp_path)
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'src="runs/sample/picture.mp4"' in page


def test_media_stays_out_of_the_source_archive() -> None:
    assert _excluded("show-bench/corpus/program.wav")
    assert _excluded("show-bench/runs/before/picture.mp4")
    assert _excluded("notes.mp3") is True


def test_short_offline_run_uses_mock_and_does_not_arm(tmp_path: Path) -> None:
    samples, manifest = build_program()
    summary = run_session(
        samples,
        manifest["sample_rate"],
        mode="offline",
        out_dir=tmp_path / "run",
        manifest=manifest,
        limit_s=0.7,
        video=False,
    )
    assert summary["transport"] == "mock"
    assert summary["armed"] is False
    assert summary["blackout_lifted_in_stand"] is True
    assert summary["injected_into_engine"] is False
    assert summary["settings"]["period"] is None
    assert summary["frames"] >= 15
    assert summary["ear"]["worker"] is True
    assert summary["ear"]["analysis_jobs"] >= 1
    assert (tmp_path / "run" / "trace.jsonl").is_file()
    assert (tmp_path / "run" / "observations.md").is_file()
