"""One virtual day of the cloud exchange. Nothing here contacts Cloudflare."""

from orng_led.cloud.budget import (
    FREE_D1_ROWS_READ_PER_DAY,
    FREE_D1_ROW_WRITES_PER_DAY,
    FREE_DO_DURATION_GB_S_PER_DAY,
    FREE_DO_REQUESTS_PER_DAY,
    FREE_DO_SQLITE_ROWS_READ_PER_DAY,
    FREE_DO_SQLITE_ROWS_WRITTEN_PER_DAY,
    FREE_WORKER_REQUESTS_PER_DAY,
)
from orng_led.cloud.exchange import legacy_executor_http_per_day, simulate_day


def _under_free(day) -> None:
    assert day.do_requests < FREE_DO_REQUESTS_PER_DAY
    assert day.worker_requests < FREE_WORKER_REQUESTS_PER_DAY
    assert day.d1_rows_read < FREE_D1_ROWS_READ_PER_DAY
    assert day.d1_rows_written < FREE_D1_ROW_WRITES_PER_DAY
    assert day.do_sqlite_rows_read < FREE_DO_SQLITE_ROWS_READ_PER_DAY
    assert day.do_sqlite_rows_written < FREE_DO_SQLITE_ROWS_WRITTEN_PER_DAY
    assert day.do_duration_gb_s < FREE_DO_DURATION_GB_S_PER_DAY
    assert day.r2_reads == 0
    assert day.r2_writes == 0
    assert day.log_events == 0
    # The paid 20:1 figure is not the number that closes the free plan.
    assert day.do_billing_units < day.do_requests


def test_no_desk_sends_presence_only() -> None:
    day = simulate_day()
    assert day.executor_http == 0
    assert day.keepalive_forwards == 0
    assert day.command_log_inserts == 0
    # 120s presence, hibernating between beats. No preview.
    assert day.do_ws_incoming == 720
    assert day.d1_rows_written == 720 * 2
    _under_free(day)


def test_one_desk_skips_repeated_preview_and_the_old_poll() -> None:
    quiet = simulate_day(subscribers=1, windows_watch=True, has_token=True)
    busy = simulate_day(
        subscribers=1,
        windows_watch=True,
        has_token=True,
        effect_change_s=5,
    )
    assert quiet.executor_http == 0
    assert busy.executor_http == 0
    assert quiet.executor_http < legacy_executor_http_per_day()
    assert quiet.do_ws_incoming < busy.do_ws_incoming
    assert busy.do_requests < FREE_DO_REQUESTS_PER_DAY
    _under_free(quiet)
    _under_free(busy)


def test_several_desks_share_one_telemetry_stream() -> None:
    one = simulate_day(subscribers=1, effect_change_s=5)
    three = simulate_day(subscribers=3, effect_change_s=5, hold_tabs=2, hold_s=3_600)
    assert three.do_ws_accept == one.do_ws_accept + 2
    # Two holding tabs add their own refreshes. They do not each open a preview.
    assert three.keepalive_forwards > 0
    assert three.command_log_inserts == 1
    assert three.do_ws_incoming - three.keepalive_forwards < one.do_ws_incoming + 50
    _under_free(three)


def test_outage_fallback_is_bounded_and_a_missing_token_sends_nothing() -> None:
    paired = simulate_day(
        windows_watch=True,
        has_token=True,
        outage_s=600,
        outage_at_s=1_000,
    )
    laptop = simulate_day(
        windows_watch=True,
        has_token=False,
        outage_s=600,
        outage_at_s=1_000,
    )
    assert paired.executor_http == 20
    assert laptop.executor_http == 0
    assert laptop.worker_requests < paired.worker_requests
    assert paired.executor_http < legacy_executor_http_per_day()
    _under_free(paired)
    _under_free(laptop)


def test_presses_are_counted_apart_from_telemetry() -> None:
    idle = simulate_day(subscribers=1)
    played = simulate_day(subscribers=1, presses=40)
    assert played.command_log_inserts == idle.command_log_inserts + 40
    assert played.do_ws_incoming == idle.do_ws_incoming + 80
    _under_free(played)
