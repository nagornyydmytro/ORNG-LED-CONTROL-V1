"""Optional Linux USB keyboard adapter (evdev), disabled on Windows/macOS.

Operational rules:
- hotplug/reconnect without app restart;
- disconnect releases holds for that source_id only;
- mapping always from active scene controls.yaml;
- allowlist by name/vendor/product/path;
- exclusive grab off by default;
- autorepeat ignored (no duplicate press).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from orng_led.input.adapters import adapter_for_hid
from orng_led.input.contract import InputSource

if TYPE_CHECKING:
    from orng_led.api.runtime import AppRuntime

logger = logging.getLogger(__name__)

_EVDEV: Any | None
if sys.platform.startswith("linux"):
    try:
        import evdev as _EVDEV
    except ImportError:  # pragma: no cover
        _EVDEV = None
else:
    _EVDEV = None

_LINUX_KEY_TO_CODE: dict[str, str] = {
    "KEY_Q": "KeyQ",
    "KEY_W": "KeyW",
    "KEY_E": "KeyE",
    "KEY_R": "KeyR",
    "KEY_T": "KeyT",
    "KEY_Y": "KeyY",
    "KEY_U": "KeyU",
    "KEY_I": "KeyI",
    "KEY_O": "KeyO",
    "KEY_P": "KeyP",
    "KEY_Z": "KeyZ",
    "KEY_X": "KeyX",
    "KEY_C": "KeyC",
    "KEY_F": "KeyF",
    "KEY_SPACE": "Space",
    "KEY_ESC": "Escape",
    "KEY_DELETE": "Delete",
    "KEY_BACKSPACE": "Backspace",
    "KEY_ENTER": "Enter",
    "KEY_KPENTER": "NumpadEnter",
    "KEY_MINUS": "Minus",
    "KEY_EQUAL": "Equal",
    "KEY_KPMINUS": "NumpadSubtract",
    "KEY_KPPLUS": "NumpadAdd",
    "KEY_VOLUMEDOWN": "AudioVolumeDown",
    "KEY_VOLUMEUP": "AudioVolumeUp",
    "KEY_MUTE": "AudioVolumeMute",
    "KEY_MICMUTE": "AudioVolumeMute",
    # Some kernels alias Mute as KEY_MIN_INTERESTING (see pick_linux_key_name).
    "KEY_MIN_INTERESTING": "AudioVolumeMute",
}


def linux_keyboard_enabled() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    flag = os.environ.get("ORNG_LINUX_KEYBOARD", "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def exclusive_grab_enabled() -> bool:
    return os.environ.get("ORNG_LINUX_KEYBOARD_GRAB", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def normalize_linux_key_code(key_name: str) -> str | None:
    return _LINUX_KEY_TO_CODE.get(str(key_name))


def pick_linux_key_name(key_name: Any) -> str | None:
    """evdev may return a tuple of aliases, e.g. (KEY_MIN_INTERESTING, KEY_MUTE).

    Prefer a concrete alias (KEY_MUTE) over opaque ones (KEY_MIN_INTERESTING).
    """
    if isinstance(key_name, (list, tuple)):
        names = [str(x) for x in key_name]
        preferred = (
            "KEY_MUTE",
            "KEY_VOLUMEUP",
            "KEY_VOLUMEDOWN",
            "KEY_MICMUTE",
        )
        for want in preferred:
            if want in names and normalize_linux_key_code(want) is not None:
                return want
        for name in names:
            if normalize_linux_key_code(name) is not None:
                return name
        return names[0] if names else None
    if key_name is None:
        return None
    return str(key_name)


def evdev_available() -> bool:
    return _EVDEV is not None


def parse_allowlist() -> dict[str, set[str]]:
    """ORNG_LINUX_KEYBOARD_ALLOW=name:Foo|vendor:1234|product:abcd|path:/dev/input/event2"""
    raw = os.environ.get("ORNG_LINUX_KEYBOARD_ALLOW", "").strip()
    out: dict[str, set[str]] = {
        "name": set(),
        "vendor": set(),
        "product": set(),
        "path": set(),
    }
    if not raw:
        return out
    for part in raw.split("|"):
        if ":" not in part:
            continue
        kind, value = part.split(":", 1)
        kind = kind.strip().lower()
        value = value.strip()
        if kind in out and value:
            out[kind].add(value.lower() if kind != "path" else value)
    return out


def device_allowed(info: dict[str, str], allow: dict[str, set[str]]) -> bool:
    if not any(allow.values()):
        return True
    if allow["path"] and info.get("path") in allow["path"]:
        return True
    if allow["name"] and info.get("name", "").lower() in allow["name"]:
        return True
    if allow["vendor"] and info.get("vendor", "").lower() in allow["vendor"]:
        return True
    if allow["product"] and info.get("product", "").lower() in allow["product"]:
        return True
    return False


def source_id_for_path(path: str) -> str:
    safe = path.replace("/", "_").replace("\\", "_")
    return f"linux-keyboard:{safe}"


@dataclass
class LinuxKeyboardStatus:
    enabled: bool = False
    supported: bool = False
    connected: bool = False
    count: int = 0
    devices: list[dict[str, Any]] = field(default_factory=list)
    device_name: str | None = None
    device_id: str | None = None
    device_path: str | None = None
    last_event_time: float | None = None
    last_error: str | None = None
    mapping_generation: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "supported": self.supported,
            "connected": self.connected,
            "count": self.count,
            "devices": list(self.devices),
            "device_name": self.device_name,
            "device_id": self.device_id,
            "device_path": self.device_path,
            "last_event_time": self.last_event_time,
            "last_error": self.last_error,
            "mapping_generation": self.mapping_generation,
        }


@dataclass
class _DeviceSession:
    path: str
    source_id: str
    name: str
    vendor: str = ""
    product: str = ""
    task: asyncio.Task[None] | None = None
    thread: threading.Thread | None = None
    stop_thread: threading.Event = field(default_factory=threading.Event)
    device: Any = None


@dataclass
class LinuxKeyboardAdapter:
    runtime: AppRuntime
    status: LinuxKeyboardStatus = field(default_factory=LinuxKeyboardStatus)
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event | None = None
    _sessions: dict[str, _DeviceSession] = field(default_factory=dict)
    _mapping_gen: int = 0
    # Deduplicate the same HID code across CH57x dual interfaces / bounce.
    _recent_key: dict[tuple[str, str], float] = field(default_factory=dict)
    # Injectable for tests (fake evdev).
    list_devices_fn: Any | None = None
    open_device_fn: Any | None = None

    def __post_init__(self) -> None:
        self._poll_s = float(os.environ.get("ORNG_LINUX_KEYBOARD_POLL_S", "2.0"))
        self.status.enabled = linux_keyboard_enabled()
        self.status.supported = sys.platform.startswith("linux") and evdev_available()
        if self.status.enabled and not self.status.supported:
            if not sys.platform.startswith("linux"):
                self.status.last_error = "linux keyboard requires Linux"
            elif not evdev_available():
                self.status.last_error = (
                    "python-evdev not installed (pip install orng-led-control[linux])"
                )

    def notify_controls_changed(self) -> None:
        """Active scene controls.yaml replaced — bump generation; release old holds."""
        self._mapping_gen += 1
        self.status.mapping_generation = self._mapping_gen
        for session in list(self._sessions.values()):
            self._release_source(session.source_id)

    async def start(self) -> None:
        if not self.status.enabled:
            return
        if not self.status.supported and self.list_devices_fn is None:
            logger.warning("Linux keyboard enabled but unavailable: %s", self.status.last_error)
            return
        if self._task is not None:
            return
        self._stop = asyncio.Event()
        self._poll_s = float(os.environ.get("ORNG_LINUX_KEYBOARD_POLL_S", "2.0"))
        self._task = asyncio.create_task(self._supervise(), name="orng-linux-keyboard")

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        for session in list(self._sessions.values()):
            await self._stop_session(session)
        self._sessions.clear()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.status.connected = False
        self.status.count = 0
        self.status.devices = []

    def _release_source(self, source_id: str) -> None:
        if self.runtime.input_dispatcher is not None:
            self.runtime.input_dispatcher.force_release_source(source_id)

    def _adapter(self, session: _DeviceSession):
        controls = getattr(self.runtime, "scene_controls", None)
        return adapter_for_hid(controls, session.vendor, session.product)

    async def _supervise(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            try:
                await self._reconcile_devices()
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = str(exc)
                logger.warning("Linux keyboard reconcile error: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_s)
            except TimeoutError:
                continue

    async def _reconcile_devices(self) -> None:
        allow = parse_allowlist()
        found: dict[str, dict[str, str]] = {}
        # Opening every input node is slow. Keep it off the frame loop.
        for info in await asyncio.to_thread(self._discover):
            if not device_allowed(info, allow):
                continue
            found[info["path"]] = info

        # Drop disconnected.
        for path in list(self._sessions):
            if path not in found:
                await self._stop_session(self._sessions.pop(path))

        # Add new.
        for path, info in found.items():
            if path in self._sessions:
                continue
            session = _DeviceSession(
                path=path,
                source_id=source_id_for_path(path),
                name=info.get("name") or "keyboard",
                vendor=str(info.get("vendor") or ""),
                product=str(info.get("product") or ""),
            )
            self._sessions[path] = session
            session.task = asyncio.create_task(
                self._read_loop(session),
                name=f"orng-kb-{session.source_id}",
            )

        self._refresh_status()

    def _discover(self) -> list[dict[str, str]]:
        if self.list_devices_fn is not None:
            return list(self.list_devices_fn())
        if _EVDEV is None:
            return []
        out: list[dict[str, str]] = []
        try:
            paths = _EVDEV.list_devices()
        except Exception as exc:  # noqa: BLE001
            self.status.last_error = str(exc)
            return []
        for path in paths:
            try:
                device = _EVDEV.InputDevice(path)
            except Exception:  # noqa: BLE001
                continue
            try:
                caps = device.capabilities().get(_EVDEV.ecodes.EV_KEY, [])
                has_letters = (
                    _EVDEV.ecodes.KEY_A in caps and _EVDEV.ecodes.KEY_Z in caps
                )
                has_volume = any(
                    k in caps
                    for k in (
                        _EVDEV.ecodes.KEY_VOLUMEUP,
                        _EVDEV.ecodes.KEY_VOLUMEDOWN,
                        getattr(_EVDEV.ecodes, "KEY_MUTE", -1),
                    )
                )
                # CH57x consumer iface often has Volume/Mute only — no A–Z.
                if not has_letters and not has_volume:
                    device.close()
                    continue
                name = str(getattr(device, "name", "") or "keyboard")
                name_l = name.lower()
                # HDMI/CEC consumer nodes emit Mute/Volume and falsely toggle Face.
                if any(tok in name_l for tok in ("hdmi", "vc4", "cec")):
                    device.close()
                    continue
                vendor = f"{getattr(device.info, 'vendor', 0):04x}"
                product = f"{getattr(device.info, 'product', 0):04x}"
                # Skip anonymous virtual volume nodes (no USB vid/pid, no letters).
                if vendor == "0000" and product == "0000" and not has_letters:
                    device.close()
                    continue
                info = {
                    "path": path,
                    "name": name,
                    "vendor": vendor,
                    "product": product,
                    "has_letters": "1" if has_letters else "0",
                    "has_volume": "1" if has_volume else "0",
                }
                out.append(info)
            finally:
                try:
                    device.close()
                except Exception:  # noqa: BLE001
                    pass
        # CH57x splits pad / volume / mute across multiple event nodes — keep all
        # useful nodes for the same VID:PID; key debounce drops duplicate reports.
        clean: list[dict[str, str]] = []
        for info in out:
            clean.append(
                {
                    "path": info["path"],
                    "name": info["name"],
                    "vendor": info["vendor"],
                    "product": info["product"],
                }
            )
        return clean

    async def _stop_session(self, session: _DeviceSession) -> None:
        self._release_source(session.source_id)
        session.stop_thread.set()
        if session.device is not None:
            try:
                # Unblock a stuck read_loop / select by closing the fd.
                session.device.close()
            except Exception:  # noqa: BLE001
                pass
        if session.thread is not None and session.thread.is_alive():
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: session.thread.join(timeout=2.0)
            )
            session.thread = None
        if session.task is not None:
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass
            session.task = None
        session.device = None
        self._refresh_status()

    def _refresh_status(self) -> None:
        devices = [
            {"path": s.path, "name": s.name, "source_id": s.source_id}
            for s in self._sessions.values()
        ]
        self.status.devices = devices
        self.status.count = len(devices)
        self.status.connected = bool(devices)
        if devices:
            self.status.device_path = devices[0]["path"]
            self.status.device_name = devices[0]["name"]
            self.status.device_id = devices[0]["source_id"]
        else:
            self.status.device_path = None
            self.status.device_name = None
            self.status.device_id = None

    async def _read_loop(self, session: _DeviceSession) -> None:
        """Open device and pump events.

        Real Linux devices use a dedicated thread + blocking ``read_loop`` —
        uvicorn's asyncio loop does not reliably wake ``async_read_one``.
        Fake/test devices keep the async queue path.
        """
        assert self._stop is not None
        try:
            if self.open_device_fn is not None:
                device = self.open_device_fn(session.path)
            else:
                assert _EVDEV is not None
                device = _EVDEV.InputDevice(session.path)
                if exclusive_grab_enabled():
                    try:
                        device.grab()
                    except Exception as exc:  # noqa: BLE001
                        self.status.last_error = f"grab_failed:{exc}"
            session.device = device
            self._refresh_status()

            # Test doubles expose async_read_one and usually lack a real fd.
            if self.open_device_fn is not None and hasattr(device, "async_read_one"):
                await self._async_queue_read_loop(session, device)
                return

            await self._thread_read_loop(session, device)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.status.last_error = str(exc)
            logger.warning("Linux keyboard %s error: %s", session.path, exc)
        finally:
            self._release_source(session.source_id)
            session.stop_thread.set()
            if session.device is not None:
                try:
                    session.device.close()
                except Exception:  # noqa: BLE001
                    pass
                session.device = None
            self._sessions.pop(session.path, None)
            self._refresh_status()

    async def _async_queue_read_loop(self, session: _DeviceSession, device: Any) -> None:
        assert self._stop is not None
        mapping_seen = self._mapping_gen
        while not self._stop.is_set() and not session.stop_thread.is_set():
            if mapping_seen != self._mapping_gen:
                self._release_source(session.source_id)
                mapping_seen = self._mapping_gen
            try:
                event = await device.async_read_one()
            except BlockingIOError:
                await asyncio.sleep(0.01)
                continue
            except OSError as exc:
                self.status.last_error = str(exc)
                break
            if event is None:
                await asyncio.sleep(0.01)
                continue
            self._handle_event(session, event)

    async def _thread_read_loop(self, session: _DeviceSession, device: Any) -> None:
        assert self._stop is not None
        loop = asyncio.get_running_loop()
        mapping_holder = {"gen": self._mapping_gen}

        def _worker() -> None:
            try:
                while not session.stop_thread.is_set():
                    if mapping_holder["gen"] != self._mapping_gen:
                        loop.call_soon_threadsafe(self._release_source, session.source_id)
                        mapping_holder["gen"] = self._mapping_gen
                    try:
                        # Short timeout so stop/close can interrupt promptly.
                        import select as select_mod

                        ready, _, _ = select_mod.select([device.fd], [], [], 0.5)
                        if not ready:
                            continue
                        for event in device.read():
                            loop.call_soon_threadsafe(self._handle_event, session, event)
                    except OSError as exc:
                        self.status.last_error = str(exc)
                        break
                    except Exception as exc:  # noqa: BLE001
                        self.status.last_error = str(exc)
                        logger.warning("Linux keyboard %s thread error: %s", session.path, exc)
                        break
            finally:
                pass

        session.stop_thread.clear()
        session.thread = threading.Thread(
            target=_worker,
            name=f"orng-kb-{session.source_id}",
            daemon=True,
        )
        session.thread.start()
        # Park the asyncio task until stop/cancel; worker owns the fd reads.
        while not self._stop.is_set() and not session.stop_thread.is_set():
            if session.thread is not None and not session.thread.is_alive():
                break
            await asyncio.sleep(0.25)

    def _handle_event(self, session: _DeviceSession, event: Any) -> None:
        # Fake devices may pass already-normalized dicts.
        if isinstance(event, dict):
            code = event.get("code")
            event_type = event.get("type")
            repeat = bool(event.get("repeat"))
        else:
            if _EVDEV is None or event.type != _EVDEV.ecodes.EV_KEY:
                return
            key_event = _EVDEV.categorize(event)
            key_name = pick_linux_key_name(key_event.keycode)
            code = normalize_linux_key_code(key_name) if key_name else None
            if key_event.keystate == 0:
                event_type, repeat = "keyup", False
            elif key_event.keystate == 1:
                event_type, repeat = "keydown", False
            else:
                # Autorepeat — ignore to avoid duplicate presses.
                return
        if not code:
            return
        if repeat:
            return
        # Drop duplicate press/release of the same code within 60ms (dual HID iface).
        now = time.monotonic()
        dedupe_key = (str(code), str(event_type))
        prev = self._recent_key.get(dedupe_key)
        if prev is not None and now - prev < 0.06:
            return
        self._recent_key[dedupe_key] = now
        if len(self._recent_key) > 64:
            cutoff = now - 1.0
            self._recent_key = {k: t for k, t in self._recent_key.items() if t >= cutoff}
        self.status.last_event_time = now
        raw = {
            "code": code,
            "type": event_type,
            "repeat": False,
            "source_id": session.source_id,
        }
        adapter = self._adapter(session)
        adapter.source = InputSource.KEYBOARD
        events = adapter.handle_raw(raw)
        dispatcher = self.runtime.input_dispatcher
        if dispatcher is None or not events:
            return
        dispatcher.dispatch(events[0])
