"""The hall's cloud socket has to fit the free Durable Object day."""

from orng_led.cloud.budget import (
    AUTOMATIC_SHARE,
    FREE_D1_ROW_WRITES_PER_DAY,
    FREE_DO_REQUESTS_PER_DAY,
    FREE_WORKER_REQUESTS_PER_DAY,
    HEARTBEAT_INTERVAL_S,
    LINK_INTERVAL_S,
    LINK_WAIT_S,
    device_socket_messages_per_day,
    messages_per_day,
)


def test_open_socket_stays_under_the_free_daily_ceiling() -> None:
    automatic = int(FREE_DO_REQUESTS_PER_DAY * AUTOMATIC_SHARE)
    assert device_socket_messages_per_day() <= automatic


def test_refused_socket_uses_the_slow_interval() -> None:
    """The live reserve posts on the 20s interval. It does not hold a request."""
    assert messages_per_day(LINK_INTERVAL_S) <= int(FREE_WORKER_REQUESTS_PER_DAY * AUTOMATIC_SHARE)
    assert messages_per_day(LINK_WAIT_S) > messages_per_day(LINK_INTERVAL_S)


def test_heartbeat_writes_stay_under_the_d1_ceiling() -> None:
    automatic = int(FREE_D1_ROW_WRITES_PER_DAY * AUTOMATIC_SHARE)
    assert messages_per_day(HEARTBEAT_INTERVAL_S) <= automatic
