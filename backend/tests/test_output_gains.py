"""Per-fixture output_gains compensate weak hardware colour channels."""

from __future__ import annotations

from orng_led.config import load_show_config
from orng_led.config.models import ChannelRole
from orng_led.config.validation import global_channel
from orng_led.engine.frame import empty_frame
from orng_led.engine.intents import ParIntent, Rgbw
from orng_led.engine.renderer import render_par


def _red_byte(show, frame: list[int], fixture_id: str) -> int:
    fixture = next(fx for fx in show.patch.fixtures if fx.id == fixture_id)
    profile = show.profile_for(fixture)
    red = next(ch for ch in profile.channels if ch.role is ChannelRole.RED)
    return frame[global_channel(fixture.start_address, red.local) - 1]


def test_par2_par3_have_red_gain_two() -> None:
    show = load_show_config()
    for fixture_id in ("par_2", "par_3"):
        fixture = next(fx for fx in show.patch.fixtures if fx.id == fixture_id)
        assert fixture.output_gains.get("red") == 2.0
    par_1 = next(fx for fx in show.patch.fixtures if fx.id == "par_1")
    assert not par_1.output_gains.get("red")


def test_par2_red_is_doubled_vs_par1_until_clip() -> None:
    show = load_show_config()
    intent = ParIntent(color=Rgbw(r=0.4, g=0.0, b=0.0), intensity=1.0)
    frame = empty_frame()
    for fixture_id in ("par_1", "par_2", "par_3", "par_4"):
        fixture = next(fx for fx in show.patch.fixtures if fx.id == fixture_id)
        render_par(frame, fixture, show.profile_for(fixture), intent)

    red_1 = _red_byte(show, frame, "par_1")
    red_2 = _red_byte(show, frame, "par_2")
    red_3 = _red_byte(show, frame, "par_3")
    red_4 = _red_byte(show, frame, "par_4")
    assert red_1 == round(0.4 * 255)
    assert red_4 == red_1
    assert red_2 == min(255, round(0.4 * 2.0 * 255))
    assert red_3 == red_2
    assert red_2 == 2 * red_1 or abs(red_2 - 2 * red_1) <= 1
