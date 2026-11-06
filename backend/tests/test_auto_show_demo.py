"""Home Auto-show demo is labeled, has audio, and is not an operator page."""

from __future__ import annotations

from pathlib import Path

from orng_led.audio.show_demo import build_demo, dump_html, html_text


def test_demo_labels_script_apart_from_analysis(tmp_path: Path) -> None:
    payload = build_demo()
    kinds = {chapter["kind"] for chapter in payload["chapters"]}
    assert kinds == {"script", "analysis", "sync"}
    html = html_text()
    path = dump_html(tmp_path / "orng-auto-show.html")
    text = path.read_text(encoding="utf-8")
    assert "Сценарна демонстрація" in text
    assert "Аналіз" in text
    assert "Прийнятий темп" in text
    assert "audio/wav" in text
    assert "Тримає мотив" in text
    assert "Змінює рух" in text
    assert "Строб дозволено" in text
    assert "Не пульт" in html
    assert "script" in text and "analysis" in text and "sync" in text
    assert "http://127.0.0.1:8766/" in payload["listen"]
    script = next(chapter for chapter in payload["chapters"] if chapter["kind"] == "script")
    analysis = [chapter for chapter in payload["chapters"] if chapter["kind"] == "analysis"]
    sync = next(chapter for chapter in payload["chapters"] if chapter["kind"] == "sync")
    assert analysis
    assert any(frame["motion"] == "hold" for frame in script["frames"])
    assert any(frame["motion"] in {"move", "accent"} for frame in script["frames"])
    roll = next(chapter for chapter in analysis if chapter["id"] == "analysis_roll")
    assert any(frame["motion"] == "strobe" for frame in roll["frames"])
    assert all("сітка задана" in chapter["note"].lower() for chapter in analysis)
    assert "не підставлено" in sync["note"]
    assert "data:audio/wav" in script["audio"]
    assert all("data:audio/wav" in chapter["audio"] for chapter in analysis)
    assert "data:audio/wav" in sync["audio"]
    assert any(frame.get("bpm") is not None for frame in sync["frames"])
    assert sync["highlight_s"] is not None
    assert "Показовий фрагмент" in text
    assert path.stat().st_size > 8000
