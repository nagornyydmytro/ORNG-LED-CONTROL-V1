"""The live preview keeps one picture. It does not queue stale frames."""

from orng_led.cloud.preview import PreviewOutbox


def test_a_slow_sender_keeps_only_the_newest_picture() -> None:
    box = PreviewOutbox()
    for index in range(30):
        box.offer({"n": index, "stage": {"pars": [{"id": "par_1", "intensity": index / 30}]}})
    first = box.start()
    assert first is not None
    assert first["n"] == 29
    assert box.replaced == 29
    newer = {"n": 40, "stage": {"pars": []}}
    box.offer(newer)
    assert box.start() is None
    nxt = box.finish(120)
    assert nxt is newer
    assert box.finish(80) is None
    assert box.sent == 2
    assert box.bytes_sent == 200


def test_two_desks_read_one_packet() -> None:
    box = PreviewOutbox()
    box.offer({"n": 1, "bytes": 40})
    packet = box.start()
    assert packet is not None
    desks = [dict(packet), dict(packet)]
    assert desks[0] == desks[1]
    assert box.sent == 0
    box.finish(40)
    assert box.sent == 1
    assert box.bytes_sent == 40
