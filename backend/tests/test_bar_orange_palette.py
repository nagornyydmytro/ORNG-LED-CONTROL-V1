"""warm_orange presets must drive bar orange slot, not yellower amber."""

from __future__ import annotations

from orng_led.config import default_config_dir, load_show_config
from orng_led.config.models import ChannelRole, FixtureKind
from orng_led.config.validation import global_channel
from orng_led.engine.intents import BarIntent, Rgbw, StageIntent
from orng_led.engine.renderer import _nearest_palette_name, _palette_dmx, render_stage
from orng_led.presets.program import PALETTE_RGBW


def test_warm_orange_nearest_is_orange_when_available() -> None:
    color = PALETTE_RGBW["warm_orange"]
    assert _nearest_palette_name(color, available={"red", "orange", "amber"}) == "orange"
    assert _nearest_palette_name(color, available={"red", "amber"}) == "amber"
    amber = PALETTE_RGBW["amber"]
    assert _nearest_palette_name(amber, available={"red", "orange", "amber"}) == "amber"


def test_bar_profile_warm_orange_writes_orange_dmx_not_amber() -> None:
    show = load_show_config(default_config_dir())
    bar = next(fx for fx in show.patch.fixtures if fx.kind is FixtureKind.BAR)
    channel = next(
        ch for ch in show.profile_for(bar).channels if ch.role is ChannelRole.SEGMENT_COLOR
    )
    assert channel.palette is not None and "orange" in channel.palette
    warm = PALETTE_RGBW["warm_orange"]
    dmx = _palette_dmx(channel, warm, active=True)
    assert dmx == int(channel.palette["orange"])
    assert dmx != int(channel.palette["amber"])

    stage = StageIntent(
        fixtures={
            bar.id: BarIntent(
                segments=(1.0,) * 8,
                dimmer=1.0,
                color=warm,
                whole=True,
            )
        }
    )
    frame = render_stage(show, stage, {})
    idx = global_channel(bar.start_address, channel.local) - 1
    assert frame[idx] == int(channel.palette["orange"])
