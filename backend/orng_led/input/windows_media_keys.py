"""Windows low-level hooks for hardware encoders.

- Volume Up/Down → program speed ×0.5–×5, or live-FX speed while Strobe/Sweep held
- Ctrl + mouse wheel → episode ±1 (same gesture as browser zoom; page zoom swallowed)

Browsers often never see these events. For a local DJ console we intercept them
globally while the backend is running.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_CONTROL = 0x11
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_MOUSEWHEEL = 0x020A
HC_ACTION = 0
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01


@dataclass
class WindowsEncoderHooks:
    """Background WH_KEYBOARD_LL + WH_MOUSE_LL listeners (Windows only)."""

    on_volume_up: Callable[[], None]
    on_volume_down: Callable[[], None]
    on_zoom_up: Callable[[], None]
    on_zoom_down: Callable[[], None]
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _kb_hook: int = field(default=0, init=False, repr=False)
    _mouse_hook: int = field(default=0, init=False, repr=False)
    _user32: Any = field(default=None, init=False, repr=False)
    _kb_proc: Any = field(default=None, init=False, repr=False)
    _mouse_proc: Any = field(default=None, init=False, repr=False)
    _last_zoom_at: float = field(default=0.0, init=False, repr=False)

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        if self.active:
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="orng-encoder-hooks",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        user32 = self._user32
        if user32 is not None:
            for hook_id in (self._kb_hook, self._mouse_hook):
                if hook_id:
                    try:
                        user32.UnhookWindowsHookEx(hook_id)
                    except Exception:  # noqa: BLE001
                        pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None
        self._kb_hook = 0
        self._mouse_hook = 0

    def _ctrl_down(self, user32: Any) -> bool:
        return bool(
            user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
            or user32.GetAsyncKeyState(VK_LCONTROL) & 0x8000
            or user32.GetAsyncKeyState(VK_RCONTROL) & 0x8000
        )

    def _emit_zoom(self, up: bool) -> None:
        now = time.monotonic()
        # One notch of a physical wheel often fires multiple messages; keep it snappy.
        if now - self._last_zoom_at < 0.08:
            return
        self._last_zoom_at = now
        try:
            if up:
                self.on_zoom_up()
            else:
                self.on_zoom_down()
        except Exception:  # noqa: BLE001
            logger.exception("zoom encoder hook failed")

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32 = user32

        # Pointer-sized types — avoid OverflowError on 64-bit LPARAMs.
        LRESULT = ctypes.c_ssize_t
        HHOOK = ctypes.c_void_p
        ULONG_PTR = ctypes.c_size_t

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR),
            ]

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR),
            ]

        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            HOOKPROC,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = HHOOK
        user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [HHOOK]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = wintypes.SHORT
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = LRESULT

        def _pass(n_code: int, w_param: int, l_param: int) -> int:
            return int(user32.CallNextHookEx(None, n_code, w_param, l_param) or 0)

        def _kb_callback(n_code: int, w_param: int, l_param: int) -> int:
            if n_code == HC_ACTION and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if info.flags & LLKHF_INJECTED:
                    return _pass(n_code, w_param, l_param)
                if info.vkCode == VK_VOLUME_UP:
                    try:
                        self.on_volume_up()
                    except Exception:  # noqa: BLE001
                        logger.exception("volume-up hook failed")
                    return 1
                if info.vkCode == VK_VOLUME_DOWN:
                    try:
                        self.on_volume_down()
                    except Exception:  # noqa: BLE001
                        logger.exception("volume-down hook failed")
                    return 1
            return _pass(n_code, w_param, l_param)

        def _mouse_callback(n_code: int, w_param: int, l_param: int) -> int:
            if n_code == HC_ACTION and w_param == WM_MOUSEWHEEL and self._ctrl_down(user32):
                info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if info.flags & LLMHF_INJECTED:
                    return _pass(n_code, w_param, l_param)
                # HIWORD(mouseData) is signed wheel delta.
                raw = (info.mouseData >> 16) & 0xFFFF
                if raw >= 0x8000:
                    raw -= 0x10000
                if raw != 0:
                    # Positive Windows delta = wheel away = browser zoom-in = next episode.
                    self._emit_zoom(up=raw > 0)
                return 1  # swallow — prevent browser/OS page zoom
            return _pass(n_code, w_param, l_param)

        self._kb_proc = HOOKPROC(_kb_callback)
        self._mouse_proc = HOOKPROC(_mouse_callback)
        kb = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, None, 0)
        mouse = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, None, 0)
        self._kb_hook = int(ctypes.cast(kb, ctypes.c_void_p).value or 0) if kb else 0
        self._mouse_hook = int(ctypes.cast(mouse, ctypes.c_void_p).value or 0) if mouse else 0
        if not kb and not mouse:
            err = ctypes.get_last_error()
            logger.warning("Windows encoder hooks failed to install (last_error=%s)", err)
            return

        logger.info(
            "Windows encoder hooks active (volume→speed, Ctrl+wheel→episodes)%s%s",
            "" if kb else " [volume missing]",
            "" if mouse else " [wheel missing]",
        )
        msg = wintypes.MSG()
        while not self._stop.is_set():
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.01)

        if kb:
            user32.UnhookWindowsHookEx(kb)
        if mouse:
            user32.UnhookWindowsHookEx(mouse)
        self._kb_hook = 0
        self._mouse_hook = 0


# Back-compat alias for older imports.
WindowsVolumeKeyHook = WindowsEncoderHooks


def start_volume_hook_for_runtime(runtime: Any) -> WindowsEncoderHooks | None:
    """Attach Windows volume + Ctrl+wheel hooks to ``runtime`` if possible."""
    if sys.platform != "win32":
        return None

    from orng_led.input.contract import PROGRAM_SPEED_STEP

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    def _broadcast() -> None:
        if loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(runtime.broadcast_state(), loop)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to broadcast after encoder nudge")

    def _nudge_speed(delta: float) -> None:
        # Routes to live-FX speed when Strobe/Sweep is held (see runtime).
        runtime.apply_nudge_program_speed(delta, client_command_id=None)
        _broadcast()

    def _nudge_zoom(delta: int) -> None:
        # Same path as ZoomIn/ZoomOut keys: always episode ±1 (FX stays held).
        runtime.apply_zoom(delta, client_command_id=None)
        _broadcast()

    hook = WindowsEncoderHooks(
        on_volume_up=lambda: _nudge_speed(PROGRAM_SPEED_STEP),
        on_volume_down=lambda: _nudge_speed(-PROGRAM_SPEED_STEP),
        on_zoom_up=lambda: _nudge_zoom(1),
        on_zoom_down=lambda: _nudge_zoom(-1),
    )
    if hook.start():
        runtime._volume_key_hook = hook  # noqa: SLF001 — lifecycle ownership
        return hook
    return None


def stop_volume_hook_for_runtime(runtime: Any) -> None:
    hook = getattr(runtime, "_volume_key_hook", None)
    if hook is not None:
        hook.stop()
        runtime._volume_key_hook = None  # noqa: SLF001
