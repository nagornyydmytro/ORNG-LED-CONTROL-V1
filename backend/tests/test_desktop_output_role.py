from orng_led.desktop.output_role import desktop_output_role, local_artnet_allowed


def test_unset_role_does_not_block_explicit_activation(monkeypatch) -> None:
    monkeypatch.delenv("ORNG_DESKTOP_OUTPUT", raising=False)
    monkeypatch.delenv("ORNG_CLOUD_DEVICE_MODE", raising=False)
    assert desktop_output_role() == "unset"
    assert local_artnet_allowed() is True


def test_product_role_keeps_artnet_on_the_raspberry(monkeypatch) -> None:
    monkeypatch.setenv("ORNG_DESKTOP_OUTPUT", "raspberry")
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_MODE", "desktop")
    assert local_artnet_allowed() is False


def test_explicit_local_output(monkeypatch) -> None:
    monkeypatch.setenv("ORNG_DESKTOP_OUTPUT", "local")
    assert local_artnet_allowed() is True


def test_cloud_drop_does_not_change_role(monkeypatch) -> None:
    monkeypatch.setenv("ORNG_DESKTOP_OUTPUT", "raspberry")
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_MODE", "desktop")
    assert local_artnet_allowed() is False
