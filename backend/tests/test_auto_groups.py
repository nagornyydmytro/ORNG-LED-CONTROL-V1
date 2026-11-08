"""Independent Auto classes and excluded compositions. Output stays disarmed."""

from orng_led.audio.library import BY_ID
from orng_led.audio.service import BpmService
from orng_led.config.models import FixtureKind
from orng_led.engine.intents import BarIntent, BeamIntent, ParIntent


def _service(tmp_path):
    service = BpmService(tmp_path, "groups")
    service.configure(enabled=True, scope="auto", palette="orange", character="rhythmic")
    return service


def test_removed_bar_chases_are_gone() -> None:
    for name in (
        "vertical_wave_scan_bars",
        "cascade_left",
        "cascade_right",
        "cascade_bars_then_segments",
    ):
        assert name not in BY_ID


def test_each_class_can_be_off_without_becoming_all(tmp_path) -> None:
    service = _service(tmp_path)
    service.configure(groups=[])
    stage = service.look([], None)
    assert stage.fixtures == {} or all(
        isinstance(intent, BeamIntent) and intent.dimmer == 0.0 for intent in stage.fixtures.values()
    )
    service.configure(groups=["bar"])
    assert service.groups == ("bar",)
    assert service.cast != "all" or service.groups == ("bar",)


def test_old_cast_keeps_its_classes(tmp_path) -> None:
    service = _service(tmp_path)
    service.configure(cast="bars")
    assert service.groups == ("bar",)
    service.configure(cast="heads")
    assert service.groups == ("par", "beam")
    service.configure(cast="all")
    assert service.groups == ("par", "bar", "beam")


def test_disabled_bar_does_not_return_through_the_bed(tmp_path) -> None:
    from orng_led.config.io import load_show_config

    show = load_show_config()
    service = _service(tmp_path)
    service.configure(groups=["par", "beam"], par_rhythm=True, accent=1.0)
    service.section.extra_pulse = True
    service.follower.locked = True
    stage = service.look(list(show.patch.fixtures), show.layout.placements)
    assert not any(isinstance(intent, BarIntent) for intent in stage.fixtures.values())


def test_heads_off_holds_the_aim(tmp_path) -> None:
    from orng_led.config.io import load_show_config

    show = load_show_config()
    service = _service(tmp_path)
    service.configure(groups=["par", "bar", "beam"])
    service.look(list(show.patch.fixtures), show.layout.placements)
    before = service.heads.pose("beam_left")
    service.configure(groups=["par", "bar"])
    stage = service.look(list(show.patch.fixtures), show.layout.placements)
    after = service.heads.pose("beam_left")
    if before is not None and after is not None:
        assert after == before
    beam = stage.fixtures.get("beam_left")
    if isinstance(beam, BeamIntent):
        assert beam.dimmer == 0.0
        assert beam.pan != 0.0 or beam.pan is None


def test_unchecking_the_live_look_replaces_it_before_the_next_beat(tmp_path) -> None:
    from orng_led.config.io import load_show_config

    show = load_show_config()
    service = _service(tmp_path)
    service.follower.locked = True
    service.follower._beats = 1.4
    service.look(list(show.patch.fixtures), show.layout.placements)
    current = service.planner.current
    assert current is not None and current.look
    banned = current.look
    service.configure(excluded=[banned])
    assert service.planner.current is not None
    assert service.planner.current.look != banned
    assert service.planner.current.start_beat <= 1.4 + 1e-6


def test_excluded_look_is_not_the_fallback(tmp_path) -> None:
    service = _service(tmp_path)
    service.planner.set_excluded(("wash",))
    assert service.planner._pick("signal", "orange") != "wash"
    assert "wash" not in service.planner._allowed_ids("orange") or "wash" in service.planner.excluded
