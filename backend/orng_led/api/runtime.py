"""Single-process application runtime: one engine, one output, WS fan-out."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from orng_led.api.schemas import (
    AppStateResponse,
    EngineState,
    OutputState,
)
from orng_led.config import (
    collect_patch_errors,
    default_config_dir,
    load_show_config,
    save_model,
)
from orng_led.config.io import is_scene_directory, parse_model
from orng_led.config.models import (
    AppConfig,
    ChannelRole,
    ConfigError,
    FixtureKind,
    FixtureProfile,
    PatchDocument,
    ShowConfig,
    SpatialLayout,
    TransportMode,
)
from orng_led.config.validation import global_channel
from orng_led.engine.clock import FRAME_DT
from orng_led.engine.engine import Engine, EngineSnapshot
from orng_led.engine.intents import Rgbw
from orng_led.input.dispatcher import InputDispatcher
from orng_led.output.contract import OutputError
from orng_led.output.controller import OutputController
from orng_led.presets.models import PresetDocument
from orng_led.presets.store import PresetStore
from orng_led.scenes.models import SceneControls, SceneManifest
from orng_led.scenes.store import SceneStore
from orng_led.setup.beam_calibration import BeamCalibrationTestSession
from orng_led.setup.raw_tester import RawTesterSession
from orng_led.simulator.decode import decode_simulator_view

logger = logging.getLogger(__name__)

WsSender = Callable[[dict], Awaitable[None]]


class ManualShowLockedError(Exception):
    """Auto owns the look. The command was not applied."""

    status = "rejected"


class CommandConflictError(Exception):
    """Same command_id arrived again with different content."""

    status = "rejected"
    code = "command_conflict"


def _begin_frame_timer() -> bool:
    """Windows sleeps in 15.6 ms steps unless the multimedia timer is 1 ms.

    One begin pairs with one end. Other platforms already wake on a fine clock.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return int(ctypes.windll.winmm.timeBeginPeriod(1)) == 0
    except (AttributeError, OSError):
        return False


def _end_frame_timer(active: bool) -> None:
    if not active:
        return
    try:
        import ctypes

        ctypes.windll.winmm.timeEndPeriod(1)
    except (AttributeError, OSError):
        return


@dataclass
class AppRuntime:
    """Process-wide lighting runtime.

    Reconnecting WebSocket clients attach to this same instance and never create
    a second engine.
    """

    show: ShowConfig
    engine: Engine
    output: OutputController
    sequence: int = 0
    _subscribers: set[WsSender] = field(default_factory=set)
    _sockets: set[object] = field(default_factory=set)
    _sender_latest: dict[WsSender, dict] = field(default_factory=dict)
    _sender_tasks: dict[WsSender, asyncio.Task[None]] = field(default_factory=dict)
    _last_engine_snap: object | None = None
    _seen_commands: OrderedDict[str, str] = field(default_factory=OrderedDict)
    _loop_task: asyncio.Task[None] | None = None
    _aio_loop: asyncio.AbstractEventLoop | None = None
    _broadcast_event: threading.Event = field(default_factory=threading.Event)
    _broadcast_thread: threading.Thread | None = None
    _tick_samples: deque[float] = field(default_factory=lambda: deque(maxlen=4000))
    _engine_samples: deque[float] = field(default_factory=lambda: deque(maxlen=4000))
    _send_samples: deque[float] = field(default_factory=lambda: deque(maxlen=4000))
    _poll_samples: deque[float] = field(default_factory=lambda: deque(maxlen=4000))
    _state_samples: deque[float] = field(default_factory=lambda: deque(maxlen=2000))
    _lag_samples: deque[float] = field(default_factory=lambda: deque(maxlen=4000))
    _sleep_samples: deque[float] = field(default_factory=lambda: deque(maxlen=4000))
    _scheduler_samples: deque[float] = field(default_factory=lambda: deque(maxlen=4000))
    _running: bool = False
    _shutting_down: bool = False
    _frame_cycle_failed: bool = False
    safe_dark_delivery: str | None = None
    _last_wall: float | None = None
    _last_ws_broadcast: float | None = None
    autostart_loop: bool = True
    # UI/WebSocket refresh; Art-Net keeps full engine FPS via tick().
    _ws_broadcast_dt: float = 1.0 / 15.0
    _desk_frame: list[int] | None = None
    _desk_at: float | None = None
    _desk_session: str = field(default_factory=lambda: uuid.uuid4().hex)
    _desk_sequence: int = 0
    _desk_program: str = "smooth"
    _desk_look: str | None = None
    max_idempotency_keys: int = 256
    preview_speed: float = 1.0
    _manual_speed: float | None = None
    raw_tester: RawTesterSession = field(default_factory=RawTesterSession)
    beam_calibration: BeamCalibrationTestSession = field(default_factory=BeamCalibrationTestSession)
    config_dir: Path = field(default_factory=default_config_dir)
    # Repo config root (runtime.yaml + scenes/); may differ from scene config_dir.
    config_root: Path = field(default_factory=default_config_dir)
    preset_store: PresetStore = field(default_factory=PresetStore)
    input_dispatcher: InputDispatcher | None = None
    scene_id: str = "orng-bar"
    scene_name: str = "ORNG BAR"
    scene_controls: SceneControls = field(default_factory=SceneControls)
    scene_error: str | None = None
    _linux_keyboard: object | None = None
    _cloud_agent: object | None = None
    # Authoritative editor hardware-preview session (not persisted across restart).
    _editor_preview_meta: dict[str, object] = field(default_factory=dict)
    # Venue Raspberry: Art-Net + arm under blackout with retry supervisor.
    _venue_ready_task: asyncio.Task[None] | None = None
    _venue_ready_nudge: asyncio.Event | None = None
    _venue_status: object | None = None
    # Set for the duration of a scene swap so venue_ready cannot re-arm early.
    scene_apply_in_progress: bool = False
    # One structural event steps the episode once. A manual seek retires older ones.
    _musical_pending: object | None = None
    _musical_seen: set[int] = field(default_factory=set)
    _musical_seen_order: list[int] = field(default_factory=list)
    _musical_manual_at: float = -1.0
    _travel_generation: int = 0
    _travel_revision: str = ""
    _travel_worker: object | None = None
    _travel_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def bpm(self):  # type: ignore[no-untyped-def]
        service = getattr(self, "_bpm_service", None)
        if service is None:
            from orng_led.audio.service import BpmService

            service = BpmService(self.config_root, self.scene_id)
            self._bpm_service = service
        return service

    @classmethod
    def create(
        cls,
        *,
        autostart_loop: bool = True,
        show: ShowConfig | None = None,
        config_dir: Path | None = None,
    ) -> AppRuntime:
        given = Path(config_dir) if config_dir is not None else None
        config_root = default_config_dir()
        scene_dir: Path
        manifest = SceneManifest(scene_id="orng-bar", name="ORNG BAR")
        controls = SceneControls()
        scene_error: str | None = None

        if show is not None:
            # Explicit show injection (tests): treat config_dir as scene dir when given.
            loaded = show
            scene_dir = given or default_config_dir()
            if given is not None and is_scene_directory(given):
                scene_dir = given
                try:
                    from orng_led.config.io import load_controls

                    loaded_controls = load_controls(given / "controls.yaml")
                    if loaded_controls is not None:
                        controls = loaded_controls
                    if (given / "scene.yaml").is_file():
                        from orng_led.config.io import read_yaml

                        manifest = parse_model(
                            SceneManifest,
                            read_yaml(given / "scene.yaml"),
                            source=given / "scene.yaml",
                        )
                except ConfigError:
                    pass
            config_root = (
                given.parent.parent
                if (
                    given is not None
                    and is_scene_directory(given)
                    and given.parent.name == "scenes"
                )
                else (given or config_root)
            )
        elif given is not None and is_scene_directory(given):
            # Direct scene directory (isolated test copies).
            scene_dir = given
            loaded = load_show_config(scene_dir)
            config_root = given.parent.parent if given.parent.name == "scenes" else given
            try:
                from orng_led.config.io import load_controls, read_yaml

                loaded_controls = load_controls(scene_dir / "controls.yaml")
                if loaded_controls is not None:
                    controls = loaded_controls
                if (scene_dir / "scene.yaml").is_file():
                    manifest = parse_model(
                        SceneManifest,
                        read_yaml(scene_dir / "scene.yaml"),
                        source=scene_dir / "scene.yaml",
                    )
                else:
                    manifest = SceneManifest(scene_id=scene_dir.name, name=scene_dir.name)
            except ConfigError:
                manifest = SceneManifest(scene_id=scene_dir.name, name=scene_dir.name)
        else:
            # Config root or default: load active scene via SceneStore.
            # Missing/corrupt active scene → degraded safe bootstrap (no ORNG BAR embed).
            config_root = given or default_config_dir()
            store = SceneStore(config_root)
            try:
                bundle = store.load_active_scene()
                loaded = bundle.show
                scene_dir = Path(bundle.scene_dir)
                manifest = bundle.manifest
                controls = bundle.controls
            except (ConfigError, OSError, ValueError) as exc:
                from orng_led.scenes.bootstrap_show import (
                    empty_safe_controls,
                    empty_safe_manifest,
                    empty_safe_show,
                )

                loaded = empty_safe_show()
                scene_dir = config_root / "scenes" / "_bootstrap_empty"
                scene_dir.mkdir(parents=True, exist_ok=True)
                (scene_dir / "presets").mkdir(parents=True, exist_ok=True)
                manifest = empty_safe_manifest()
                controls = empty_safe_controls()
                scene_error = f"safe_bootstrap: {exc}"

        preset_store = PresetStore.load(scene_dir / "presets")
        engine = Engine(show=loaded, presets=preset_store.programs())
        # Canon §5.4: start Mock + blackout / zero safe frame.
        engine.set_blackout(True)
        output = OutputController(engine=engine)
        runtime = cls(
            show=loaded,
            engine=engine,
            output=output,
            autostart_loop=autostart_loop,
            config_dir=scene_dir,
            config_root=config_root,
            preset_store=preset_store,
            scene_id=manifest.scene_id,
            scene_name=manifest.name,
            scene_controls=controls,
            scene_error=scene_error,
        )
        runtime.engine.overlays.master_brightness = loaded.app.master_brightness
        runtime.engine.set_strobe_speed(loaded.app.strobe_speed)
        runtime.engine.set_sweep_speed(loaded.app.sweep_speed)
        runtime.refresh_presets()
        runtime.input_dispatcher = InputDispatcher(runtime=runtime)
        return runtime

    def refresh_presets(self) -> None:
        from orng_led.engine.presets import NONE_PRESET_ID, NonePresetProgram

        programs = self.preset_store.programs()
        programs[NONE_PRESET_ID] = NonePresetProgram()
        previous = self.engine.active_preset_id
        self.engine.presets = programs
        self._request_head_travel()
        if previous not in self.engine.presets:
            # Prefer explicit «Без пресету» over an arbitrary fallback.
            self.engine.select_preset(NONE_PRESET_ID, reset_clock=True)

    def _tick_loop_live(self) -> bool:
        task = self._loop_task
        return bool(self._running) and not self._shutting_down and task is not None and not task.done()

    def _request_head_travel(self) -> None:
        """Install ranges for the current inputs. A live frame loop does not wait."""
        from orng_led.audio.head_motion import HeadTravelWorker, prepare_head_travel, travel_fingerprint

        documents = dict(self.preset_store.documents)
        revision = travel_fingerprint(self.show, documents, scene_id=self.scene_id)
        with self._travel_lock:
            current = self.bpm.head_travel
            if (
                current is not None
                and str(getattr(current, "scene_id", "")) == str(self.scene_id)
                and str(getattr(current, "revision", "")) == revision
            ):
                return
            self._travel_generation += 1
            generation = self._travel_generation
            self._travel_revision = revision
            self.bpm.head_travel = None
            live = self._tick_loop_live()
            if live and self._travel_worker is None:
                self._travel_worker = HeadTravelWorker()
            worker = self._travel_worker
        show = self.show
        if live and worker is not None:
            show = self.show.model_copy(deep=True)
            documents = {
                key: document.model_copy(deep=True) if hasattr(document, "model_copy") else document
                for key, document in documents.items()
            }
            worker.submit(
                (show, documents, str(self.scene_id), revision, generation, self._apply_head_travel)
            )
            return
        travel = prepare_head_travel(
            show,
            documents,
            scene_id=str(self.scene_id),
            revision=revision,
            generation=generation,
        )
        self._apply_head_travel(travel)

    def _apply_head_travel(self, travel: object) -> None:
        if travel is None:
            return
        with self._travel_lock:
            if int(getattr(travel, "generation", -1)) != self._travel_generation:
                return
            if str(getattr(travel, "scene_id", "")) != str(self.scene_id):
                return
            if str(getattr(travel, "revision", "")) != self._travel_revision:
                return
            self.bpm.install_head_travel(travel)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._shutting_down = False
        self._frame_cycle_failed = False
        self.safe_dark_delivery = None
        self._last_wall = time.monotonic()
        if self.autostart_loop:
            self._loop_task = asyncio.create_task(self._tick_loop(), name="orng-led-tick")
            # Local Windows: capture hardware volume encoder → program speed ×0.5–×5.
            # Skipped in tests (autostart_loop=False) so pytest never steals media keys.
            try:
                from orng_led.input.windows_media_keys import start_volume_hook_for_runtime

                start_volume_hook_for_runtime(self)
            except Exception:  # noqa: BLE001 — hook is optional
                pass
            try:
                from orng_led.input.linux_keyboard import LinuxKeyboardAdapter

                adapter = LinuxKeyboardAdapter(runtime=self)
                self._linux_keyboard = adapter
                await adapter.start()
            except Exception as exc:  # noqa: BLE001 — optional, never blocks Art-Net
                logger.warning("linux keyboard start failed: %s", exc)
                self._linux_keyboard = None
            try:
                from orng_led.cloud.agent import CloudDeviceAgent, cloud_enabled

                if cloud_enabled():
                    agent = CloudDeviceAgent(runtime=self)
                    self._cloud_agent = agent
                    agent.start()
            except Exception as exc:  # noqa: BLE001 — optional cloud agent
                logger.warning("cloud agent start failed: %s", exc)
                self._cloud_agent = None
            # Venue Raspberry: supervisor opens Art-Net + arms under blackout
            # (retry/backoff until eth0/route ready). Never clears blackout.
            self._start_venue_ready_supervisor()
            self.bpm.start()

    @property
    def frame_cycle_alive(self) -> bool:
        task = self._loop_task
        return (
            bool(self._running)
            and not self._shutting_down
            and not self._frame_cycle_failed
            and task is not None
            and not task.done()
        )

    @property
    def is_ready(self) -> bool:
        if not self._running or self._shutting_down or self._frame_cycle_failed:
            return False
        task = self._loop_task
        if task is not None and task.done():
            return False
        return True

    async def stop(self) -> None:
        await self.shutdown()

    def _handle_tick_failure(self, exc: BaseException) -> None:
        """Failed cycle is not live: one safe-dark attempt, disarm, no retry."""
        logger.exception("frame cycle failed: %s", exc)
        self._frame_cycle_failed = True
        self._running = False
        try:
            self.engine.set_blackout(True)
        except Exception as bo_exc:  # noqa: BLE001
            logger.warning("failed to force blackout after tick error: %s", bo_exc)
        try:
            self.safe_dark_delivery = self.output.emit_failsafe_dark()
        except Exception as dark_exc:  # noqa: BLE001
            logger.warning("failsafe dark raised: %s", dark_exc)
            self.safe_dark_delivery = "undeliverable"
            try:
                self.output.armed = False
            except Exception:  # noqa: BLE001
                pass

    async def _tick_loop(self) -> None:
        timer = _begin_frame_timer()
        try:
            while self._running and not self._shutting_down:
                loop_start = time.monotonic()
                last = self._last_wall or loop_start
                dt = max(0.0, min(0.25, loop_start - last))
                if self._last_wall is not None:
                    self._lag_samples.append(max(0.0, loop_start - last - FRAME_DT))
                self._last_wall = loop_start
                if dt > 0:
                    # Art-Net / DMX every engine frame — never wait on WebSocket.
                    tick_started = time.perf_counter()
                    self.tick(dt_s=dt)
                    self._tick_samples.append(time.perf_counter() - tick_started)
                    ws_due = (
                        self._last_ws_broadcast is None
                        or (loop_start - self._last_ws_broadcast) >= self._ws_broadcast_dt
                    )
                    if ws_due:
                        self._last_ws_broadcast = loop_start
                        self.request_broadcast()
                # Sleep only the remainder so work+sleep ≈ FRAME_DT (stable strobe).
                elapsed = time.monotonic() - loop_start
                requested = max(0.0, FRAME_DT - elapsed)
                before_sleep = time.perf_counter()
                await asyncio.sleep(requested)
                slept = time.perf_counter() - before_sleep
                self._sleep_samples.append(slept)
                self._scheduler_samples.append(max(0.0, slept - requested))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._handle_tick_failure(exc)
        finally:
            _end_frame_timer(timer)

    def _episode_index(self) -> int | None:
        from orng_led.engine.presets import NONE_PRESET_ID

        if self.engine.active_preset_id == NONE_PRESET_ID:
            return None
        preset = self.engine.active_preset
        position = getattr(preset, "cycle_position", None)
        if not callable(position):
            from orng_led.engine.presets import cycle_position

            position = cycle_position
        return int(position(self.engine.preset_elapsed_s).episode_index)

    def _bar_step(self, wall: float) -> float | None:
        if self.bpm.mode != "bar":
            return None
        index = self._episode_index()
        if index is None:
            return None
        episode_time, duration = self.engine.episode_window()
        return self.bpm.bar_delta(
            episode_index=index,
            episode_time=episode_time,
            duration=duration,
            preset_elapsed=self.engine.preset_elapsed_s,
            wall=wall,
        )

    def _set_musical_clock(self) -> None:
        """Preset tempo owns action phase. Live FX are not on this clock."""
        follower = self.bpm.follower
        if (
            self.bpm.enabled
            and self.bpm.scope == "preset"
            and (follower.locked or follower.releasing)
        ):
            self.engine.musical_beats = float(follower.action_beats_at(self.bpm.clock_s))
        else:
            self.engine.musical_beats = None

    def _episode_count(self) -> int:
        preset = self.engine.active_preset
        if hasattr(preset, "episode_count"):
            return max(0, int(preset.episode_count))
        document = getattr(preset, "document", None)
        episodes = getattr(document, "episodes", None)
        if episodes:
            return len(episodes)
        return 0

    def _musical_episode_ready(self) -> bool:
        from orng_led.audio.bpm import MIN_EPISODE_BEATS

        episode_time, _duration = self.engine.episode_window()
        bpm = self.bpm.follower.bpm if self.bpm.follower.locked else None
        if bpm:
            minimum = MIN_EPISODE_BEATS * 60.0 / float(bpm)
        else:
            minimum = 1.0
        return episode_time + 1e-6 >= minimum

    def _reset_musical_identity(self) -> None:
        """A new scene starts its own event numbers and its own clock."""
        self._musical_pending = None
        self._musical_seen.clear()
        self._musical_seen_order.clear()
        self._musical_manual_at = -1.0

    def _note_musical_ident(self, ident: int) -> bool:
        """True the first time this ident is seen. Old idents age out."""
        number = int(ident)
        if number in self._musical_seen:
            return False
        self._musical_seen.add(number)
        self._musical_seen_order.append(number)
        while len(self._musical_seen_order) > 64:
            self._musical_seen.discard(self._musical_seen_order.pop(0))
        return True

    def _ingest_musical_events(self, show_dt: float) -> None:
        """One confirmed musical change moves to the next episode inside the preset."""
        events = self.bpm.section.take_events()
        if not (self.bpm.enabled and self.bpm.scope == "preset"):
            self._musical_pending = None
            return
        for event in events:
            if getattr(event, "kind", "drop") not in {"drop", "restore", "texture"}:
                continue
            if not self._note_musical_ident(event.ident):
                continue
            if event.at_s <= self._musical_manual_at + 1e-9:
                continue
            # Several events in one take are still one step.
            self._musical_pending = event
        self._apply_musical_step(show_dt)
        self._maybe_phrase_cap(show_dt)

    def _apply_musical_step(self, show_dt: float) -> None:
        from orng_led.engine.presets import NONE_PRESET_ID

        if self._musical_pending is None:
            return
        if self.engine.active_preset_id == NONE_PRESET_ID:
            self._musical_pending = None
            return
        count = self._episode_count()
        if count <= 1:
            # Nowhere else to go. The episode clock keeps running.
            self._musical_pending = None
            return
        index = self._episode_index()
        if index is None:
            self._musical_pending = None
            return
        episode_time, duration = self.engine.episode_window()
        if episode_time + max(0.0, show_dt) >= duration - 1e-4:
            # The show clock already crosses this boundary. Do not step twice.
            self._musical_pending = None
            return
        if not self._musical_episode_ready():
            return
        self.engine.seek_episode((index + 1) % count)
        self.bpm.drop_bar_plan()
        self._musical_pending = None

    def _on_musical_boundary(self) -> bool:
        """Nearest beat. Do not wait for the end of a long authored episode."""
        follower = self.bpm.follower
        if follower.bpm is None:
            return True
        phase = float(follower.phase_at(self.bpm.clock_s)) % 1.0
        return phase < 0.08 or phase > 0.92

    def _maybe_phrase_cap(self, show_dt: float) -> None:
        """A quiet groove still yields inside the preset after 32–64 beats."""
        from orng_led.audio.bpm import PHRASE_CAP_BEATS, REFERENCE_BPM
        from orng_led.engine.presets import NONE_PRESET_ID

        if self._musical_pending is not None:
            return
        if not (self.bpm.enabled and self.bpm.scope == "preset"):
            return
        if self.bpm.mode == "bar":
            return
        follower = self.bpm.follower
        if not (follower.locked or follower.releasing):
            return
        if self.engine.active_preset_id == NONE_PRESET_ID:
            return
        count = self._episode_count()
        if count <= 1:
            return
        index = self._episode_index()
        if index is None:
            return
        episode_time, duration = self.engine.episode_window()
        authored_beats = max(2.0, float(duration) * (REFERENCE_BPM / 60.0))
        cap_beats = min(float(PHRASE_CAP_BEATS), authored_beats)
        cap_s = cap_beats * 60.0 / REFERENCE_BPM
        if float(episode_time) + max(0.0, show_dt) < cap_s - 1e-4:
            return
        if float(episode_time) + max(0.0, show_dt) >= float(duration) - 1e-4:
            return
        if not self._on_musical_boundary():
            return
        self.engine.seek_episode((index + 1) % count)
        self.bpm.drop_bar_plan()

    def _pull_beat_effects(self, wall: float) -> None:
        preset = self.engine.active_preset
        document = getattr(preset, "document", None)
        episodes = getattr(document, "episodes", None)
        if not episodes:
            return
        index = self._episode_index()
        if index is None or index >= len(episodes):
            return
        episode = episodes[index]
        bpm = self.bpm.follower.bpm
        if bpm is None:
            return
        from orng_led.audio.bpm import beat_phase_correction
        from orng_led.engine.timing import effect_rate_hz

        step = beat_phase_correction(
            self.engine.effect_phase_turns,
            self.bpm.follower.phase_at(self.bpm.clock_s),
            effect_rate_hz(float(episode.speed), str(episode.effect)),
            float(bpm),
            wall,
            str(episode.effect),
        )
        self.engine.effect_phase_turns += step

    def _tempo_holds_speed(self) -> bool:
        if not self.bpm.enabled:
            return False
        if self.bpm.scope == "auto":
            return True
        return bool(self.bpm.follower.locked)

    def _track_manual_speed(self) -> None:
        if self._tempo_holds_speed():
            if self._manual_speed is None:
                self._manual_speed = float(self.preview_speed)
            return
        if self._manual_speed is not None:
            self.preview_speed = float(self._manual_speed)
            self._manual_speed = None

    def tick(self, dt_s: float = FRAME_DT) -> EngineSnapshot:
        from orng_led.input.contract import PROGRAM_SPEED_MAX

        # Wall clock always drives safety timers.
        wall = max(0.0, dt_s)
        # Live program × (≤5): episode duration stays at ×1 wall time; × only
        # scales effect intensity/rate inside the episode. Locked BPM owns the
        # musical action period (action_beats / cycle) and the episode clock
        # (tempo / 120). It does not stretch BPM across the speed slider.
        # Editor scrub (>5, e.g. ×30/×60): keep accelerating the show timeline.
        poll_started = time.perf_counter()
        self.bpm.poll(wall)
        self._poll_samples.append(time.perf_counter() - poll_started)
        self._track_manual_speed()
        self._set_musical_clock()
        phase = self.bpm.follower.phase_at(self.bpm.clock_s)
        grid = self.bpm.follower.grid_phase(self.bpm.clock_s)
        tempo_known = self.bpm.enabled and self.bpm.follower.locked
        # Live FX keep press/drag/release and their own speed. Preset tempo
        # moves the episode, not the strobe or the sweep.
        self.engine.bpm_fx = False
        self.engine.bpm_beat_phase = float(phase)
        self.engine.bpm_bar_phase = float(grid)
        self.engine.bpm_timeline = False
        if self.bpm.enabled and self.bpm.scope == "auto":
            self.engine.auto_groups = tuple(self.bpm.groups)
            self.engine.auto_base = self.bpm.look(
                list(self.engine.show.patch.fixtures),
                self.engine.show.layout.placements,
                blackout=bool(self.engine.overlays.blackout),
                manual=bool(
                    self.engine.overlays.strobe_held
                    or self.engine.overlays.sweep_held
                    or self.engine.overlays.face_on
                    or self.engine.overlays.drop_held
                ),
                axes_busy=bool(self.engine.overlays.sweep_held),
            )
            show_dt = wall
            effect_rate_scale = 1.0
        else:
            self.engine.auto_groups = None
            self.engine.auto_base = None
            manual = max(0.0, float(self.preview_speed))
            bar_dt = self._bar_step(wall)
            factor = None if bar_dt is not None else self.bpm.show_factor(manual)
            if bar_dt is not None:
                # The bar boundary was chosen once. This step only walks toward it.
                show_dt = bar_dt
                effect_rate_scale = 1.0
                self.engine.bpm_timeline = True
            elif factor is not None:
                # Tempo scales the episode once. Effect phase is not scaled again.
                show_dt = wall * float(factor)
                effect_rate_scale = 1.0
                self.engine.bpm_timeline = True
                # Beat slew would fold a 2-beat or 4-beat cycle back onto one beat.
                if (
                    self.bpm.mode == "beat"
                    and tempo_known
                    and self.engine.musical_beats is None
                ):
                    self._pull_beat_effects(wall)
            elif float(self.preview_speed) > PROGRAM_SPEED_MAX + 1e-9:
                show_dt = wall * float(self.preview_speed)
                effect_rate_scale = 1.0
            else:
                show_dt = wall
                effect_rate_scale = max(0.0, float(self.preview_speed))
        self._ingest_musical_events(show_dt)
        engine_started = time.perf_counter()
        snapshot = self.engine.tick(
            dt_s=show_dt,
            wall_dt_s=wall,
            effect_rate_scale=effect_rate_scale,
        )
        self._engine_samples.append(time.perf_counter() - engine_started)
        self._last_engine_snap = snapshot
        send_started = time.perf_counter()
        self._publish_frame(self._published_frame(snapshot))
        self._send_samples.append(time.perf_counter() - send_started)
        self.sequence += 1
        self._remember_desk(snapshot.frame)
        return snapshot

    def _source_frame(self, snap: EngineSnapshot | None = None) -> list[int]:
        """Prepared source before wire gating (Raw / beam cal / engine).

        Calibration is re-rendered every read so a cached visible beam cannot
        bypass Blackout. Blackout also drops the visible-beam confirmation so
        the ray does not resume when Blackout lifts.
        """
        if self.beam_calibration.active:
            from orng_led.setup.beam_calibration import render_calibration_frame

            if self.engine.overlays.blackout:
                self.beam_calibration.visible_beam_confirmed = False
                self.beam_calibration.visible_beam_requested = False
            show_frame = list(snap.frame) if snap is not None else list(
                self.engine.render_at(self.engine.clock.time(), dt_s=0.0).frame
            )
            return render_calibration_frame(
                self.show,
                self.beam_calibration,
                blackout=self.engine.overlays.blackout,
                base=show_frame,
            )
        if self.raw_tester.active:
            return list(self.raw_tester.frame)
        if snap is not None:
            return list(snap.frame)
        return list(self.engine.render_at(self.engine.clock.time(), dt_s=0.0).frame)

    def _source_owner(self, source: list[int]) -> str:
        if self.beam_calibration.active:
            return "beam_calibration_test"
        if self.raw_tester.active:
            return "raw_tester"
        if any(int(value) for value in source):
            return "engine"
        return "none"

    def _published_frame(self, snap: EngineSnapshot | None = None) -> list[int]:
        """Frame fed into output before wire policy.

        Blackout is applied last in ``OutputController.wire_frame`` for every
        source. Calibration is never published from a stale cached look.
        """
        return self._source_frame(snap)

    def _wire_frame(self, snap: EngineSnapshot | None = None) -> list[int]:
        """Actual outbound frame after arm / Raw-under-Blackout wire policy."""
        return self.output.wire_frame(
            self._source_frame(snap),
            from_raw=self.raw_tester.active and not self.beam_calibration.active,
        )

    def _publish_frame(self, frame: list[int], *, from_raw: bool | None = None) -> None:
        if from_raw is None:
            raw = self.raw_tester.active and not self.beam_calibration.active
        else:
            raw = from_raw
        try:
            self.output.publish(frame, from_raw=raw)
        except OutputError as exc:
            self._on_artnet_publish_failure(exc)
            raise

    def build_state(self, *, reuse: bool = False) -> AppStateResponse:
        snap = self._last_engine_snap if reuse and self._last_engine_snap is not None else None
        if snap is None:
            snap = self.engine.render_at(self.engine.clock.time(), dt_s=0.0)
        out = self.output.status()
        source = self._source_frame(snap)
        wire = self.output.wire_frame(
            source,
            from_raw=self.raw_tester.active and not self.beam_calibration.active,
        )
        source_sum = int(sum(source))
        source_nonzero = int(sum(1 for value in source if value))
        wire_sum = int(sum(wire))
        wire_nonzero = int(sum(1 for value in wire if value))
        state = AppStateResponse(
            engine=EngineState(
                preset_id=snap.preset_id,
                preset_time_s=snap.preset_time_s,
                episode_index=snap.episode_index,
                episode_time_s=snap.episode_time_s,
                episode_count=snap.episode_count,
                cycle_duration_s=snap.cycle_duration_s,
                blackout=snap.blackout,
                face_on=snap.face_on,
                face_brightness=self.engine.overlays.face_brightness,
                strobe_held=snap.strobe_held,
                white_hit_active=snap.white_hit_active,
                master_brightness=snap.master_brightness,
                time_s=snap.time_s,
                drop_active=snap.drop_active,
                color_hit_active=snap.color_hit_active,
                sweep_active=snap.sweep_active,
                vertical_sweep_active=snap.vertical_sweep_active,
                strobe_speed=snap.strobe_speed,
                sweep_speed=snap.sweep_speed,
            ),
            output=OutputState(
                transport=out.transport.value,
                preferred_transport=self.show.app.transport.value,
                armed=out.armed,
                last_error=out.last_error,
                frames_sent=out.frames_sent,
                network_allowed=out.network_allowed,
                udp_active=out.udp_active,
                target_ip=out.target_ip,
                universe=out.universe,
                # frame_sum / nonzero_channels = wire (compat + topbar Σ).
                frame_sum=wire_sum,
                nonzero_channels=wire_nonzero,
                source_frame_sum=source_sum,
                source_nonzero_channels=source_nonzero,
                wire_frame_sum=wire_sum,
                wire_nonzero_channels=wire_nonzero,
                source_owner=self._source_owner(source),  # type: ignore[arg-type]
            ),
            presets=sorted(self.engine.presets.keys()),
            fixture_ids=[fx.id for fx in self.show.patch.fixtures],
            frame=wire,
            sequence=self.sequence,
            preview_speed=self.preview_speed,
            pad_presets=list(self.show.app.pad_presets),
            strobe_speed=self.engine.overlays.strobe_speed,
            sweep_speed=self.engine.overlays.sweep_speed,
            simulator=self._control_preview_view(wire),
            raw_tester=self.raw_tester.as_dict(),
            preset_editor_preview=self._editor_preview_state(source=source, wire=wire),
            live_effects=self._live_effects_state(source=source, wire=wire),
            beam_calibration=self._beam_calibration_state(source=source, wire=wire),
            bpm=self.bpm.status(),
            scene_id=self.scene_id,
            scene_name=self.scene_name,
        )
        from orng_led.desktop.desk_role import desk_role

        role = desk_role(self.config_root)
        state.desk_role = role
        from orng_led.cloud.agent import DESK_OPERATOR_COMMANDS
        from orng_led.cloud.device_mode import cloud_device_mode

        if cloud_device_mode().value == "raspberry":
            state.capabilities = sorted(set(DESK_OPERATOR_COMMANDS) | {"auto_director"})
        elif role != "raspberry":
            state.capabilities = sorted(set(DESK_OPERATOR_COMMANDS) | {"auto_director"})
        state.executor_online = getattr(self, "executor_online", None)
        view = getattr(self, "executor_view", None)
        stage_applied = False
        if role == "raspberry":
            # The laptop is not a second sender. Show the executor, not this engine.
            state.output.armed = False
            state.output.udp_active = False
            state.output.network_allowed = False
            state.output.transport = "mock"
            if isinstance(view, dict):
                state.executor_installation_id = (
                    str(view.get("installation_id")) if view.get("installation_id") else None
                )
                if "blackout" in view:
                    state.engine.blackout = bool(view.get("blackout"))
                if view.get("preset_id"):
                    state.engine.preset_id = str(view.get("preset_id"))
                for name in (
                    "strobe_held",
                    "sweep_active",
                    "vertical_sweep_active",
                    "face_on",
                ):
                    if name in view:
                        setattr(state.engine, name, bool(view.get(name)))
                if isinstance(view.get("strobe_speed"), (int, float)):
                    state.engine.strobe_speed = float(view["strobe_speed"])
                if isinstance(view.get("sweep_speed"), (int, float)):
                    state.engine.sweep_speed = float(view["sweep_speed"])
                if isinstance(view.get("episode_index"), int):
                    state.engine.episode_index = view["episode_index"]
                if isinstance(view.get("bpm"), dict):
                    state.bpm = view["bpm"]
                if isinstance(view.get("preview_speed"), (int, float)):
                    state.preview_speed = float(view["preview_speed"])
                if isinstance(view.get("face_brightness"), (int, float)):
                    state.engine.face_brightness = float(view["face_brightness"])
                scene = view.get("current_scene_id") or view.get("scene_id")
                if scene:
                    state.scene_id = str(scene)
                if view.get("scene_name"):
                    state.scene_name = str(view["scene_name"])
                if isinstance(view.get("pad_presets"), list):
                    state.pad_presets = [str(item) for item in view["pad_presets"]]
                raw_stage = view.get("stage")
                from orng_led.simulator.decode import SimulatorView

                if state.executor_online is True and isinstance(raw_stage, dict):
                    try:
                        state.simulator = SimulatorView.model_validate(raw_stage)
                        stage_applied = True
                    except Exception:
                        logger.debug("executor stage snapshot rejected", exc_info=True)
                if isinstance(view.get("presets"), list):
                    state.presets = [str(item) for item in view["presets"]]
                commands = view.get("operator_commands")
                flags = view.get("capabilities")
                merged: list[str] = []
                if isinstance(commands, list) and commands:
                    merged.extend(str(item) for item in commands)
                if isinstance(flags, list):
                    merged.extend(str(item) for item in flags)
                if merged:
                    state.capabilities = sorted(set(merged))
            elif state.executor_online is not True:
                state.executor_online = False
                state.output.last_error = "Виконавець локації ще не відповів. Локальний вивід не вмикався."
                state.capabilities = []
            if state.executor_online is False:
                state.output.last_error = "Виконавець локації недоступний. Локальний вивід не вмикався."
            if not stage_applied:
                from orng_led.simulator.decode import SimulatorView

                # The laptop engine is not the Pi. An empty frame is not local light.
                state.simulator = SimulatorView(
                    pars=[],
                    bars=[],
                    beams=[],
                    faces=[],
                    nonzero_channels=0,
                    blackout_visual=True,
                )
        return state

    def _beam_calibration_state(
        self,
        *,
        source: list[int] | None = None,
        wire: list[int] | None = None,
    ) -> dict[str, object]:
        from orng_led.config.models import FixtureKind
        from orng_led.engine.beam_transform import (
            encode_fixture_pan_tilt,
            pan_tilt_role_locals,
            semantic_to_physical,
        )
        from orng_led.setup.beam_calibration import visible_beam_blockers

        src = source if source is not None else self._source_frame()
        wr = wire if wire is not None else self._wire_frame()
        beams: list[dict[str, object]] = []
        for fixture in self.show.patch.fixtures:
            if fixture.kind is not FixtureKind.BEAM:
                continue
            placement = self.show.layout.placement_for(fixture.id)
            profile = self.show.profile_for(fixture)
            motion = self.engine.beam_motion.get(fixture.id)
            semantic_pan = float(motion.pan) if motion else float(fixture.spatial.home_pan)
            semantic_tilt = float(motion.tilt) if motion else float(fixture.spatial.home_tilt)
            if self.beam_calibration.active and self.beam_calibration.fixture_id == fixture.id:
                semantic_pan = float(self.beam_calibration.semantic_pan)
                semantic_tilt = float(self.beam_calibration.semantic_tilt)
            physical_pan, physical_tilt = semantic_to_physical(
                fixture.spatial, pan=semantic_pan, tilt=semantic_tilt
            )
            roles = pan_tilt_role_locals(profile)
            encoded = encode_fixture_pan_tilt(
                fixture, profile, semantic_pan=semantic_pan, semantic_tilt=semantic_tilt
            )
            confirmed = bool(fixture.spatial.beam_calibration_confirmed)
            blocker = None
            if not confirmed:
                blocker = "Не відкалібровано — фізичний світ Beam заблоковано"
            beams.append(
                {
                    "fixture_id": fixture.id,
                    "label": fixture.label,
                    "side": fixture.spatial.side.value,
                    "start_address": fixture.start_address,
                    "mount": placement.mount.value if placement else None,
                    "calibration_confirmed": confirmed,
                    "pan_invert": fixture.spatial.pan_invert,
                    "tilt_invert": fixture.spatial.tilt_invert,
                    "pan_offset": fixture.spatial.pan_offset,
                    "tilt_offset": fixture.spatial.tilt_offset,
                    "pan_min": fixture.spatial.pan_min,
                    "pan_max": fixture.spatial.pan_max,
                    "tilt_min": fixture.spatial.tilt_min,
                    "tilt_max": fixture.spatial.tilt_max,
                    "home_pan": fixture.spatial.home_pan,
                    "home_tilt": fixture.spatial.home_tilt,
                    "max_pan_speed": fixture.spatial.max_pan_speed,
                    "max_tilt_speed": fixture.spatial.max_tilt_speed,
                    "notes": fixture.spatial.beam_calibration_notes,
                    "semantic_pan": semantic_pan,
                    "semantic_tilt": semantic_tilt,
                    "physical_pan": physical_pan,
                    "physical_tilt": physical_tilt,
                    "roles": roles,
                    "encoded": {
                        "pan_coarse": encoded["pan"].coarse,  # type: ignore[union-attr]
                        "pan_fine": encoded["pan"].fine,  # type: ignore[union-attr]
                        "tilt_coarse": encoded["tilt"].coarse,  # type: ignore[union-attr]
                        "tilt_fine": encoded["tilt"].fine,  # type: ignore[union-attr]
                        "pan_16bit": encoded["pan"].value_16bit,  # type: ignore[union-attr]
                        "tilt_16bit": encoded["tilt"].value_16bit,  # type: ignore[union-attr]
                    },
                    "physical_output_blocker": blocker,
                }
            )
        session = self.beam_calibration.as_dict()
        if self.beam_calibration.active and self.beam_calibration.fixture_id:
            fx = next(
                (f for f in self.show.patch.fixtures if f.id == self.beam_calibration.fixture_id),
                None,
            )
            if fx is not None:
                session["visible_beam_blockers"] = visible_beam_blockers(
                    self.show, fx, blackout=self.engine.overlays.blackout
                )
        return {
            "session": session,
            "beams": beams,
            "source_nonzero_channels": int(sum(1 for value in src if value)),
            "wire_nonzero_channels": int(sum(1 for value in wr if value)),
            "source_owner": self._source_owner(src),
        }

    def _composed_stage_now(self):
        """Rebuild the composed StageIntent used for coverage / debug (no beam step)."""
        from orng_led.engine.intents import StageIntent
        from orng_led.engine.layers import compose_layers
        from orng_led.engine.presets import NONE_PRESET_ID

        engine = self.engine
        time_s = engine.clock.time()
        if engine.editor_preview_program is not None:
            base = engine.editor_preview_program.evaluate(
                engine.editor_preview_elapsed_s, engine.show
            )
        elif engine.active_preset_id == NONE_PRESET_ID:
            base = StageIntent()
        else:
            base = engine.active_preset.evaluate(engine.preset_elapsed_s, engine.show)
        if engine.overlays.blackout:
            base = StageIntent()
        return compose_layers(base, engine.show, engine.overlays, time_s)

    def _live_effects_state(
        self,
        *,
        source: list[int] | None = None,
        wire: list[int] | None = None,
    ) -> dict[str, object]:
        from orng_led.engine.layers import (
            active_live_effect_ids,
            target_fixture_ids_for_effect,
        )
        from orng_led.engine.renderer import analyze_live_effect_coverage

        now = self.engine.clock.time()
        active_ids = active_live_effect_ids(self.engine.overlays, now)
        src = source if source is not None else self._source_frame()
        wr = wire if wire is not None else self._wire_frame()
        effects: list[dict[str, object]] = []
        if active_ids:
            stage = self._composed_stage_now()
            for effect_id in active_ids:
                targets = target_fixture_ids_for_effect(effect_id, self.show)
                effects.append(analyze_live_effect_coverage(self.show, stage, effect_id, targets))
        warnings = []
        for effect in effects:
            for skip in effect.get("skipped", []):  # type: ignore[union-attr]
                if not isinstance(skip, dict):
                    continue
                missing = ", ".join(str(m) for m in skip.get("missing", []))
                label = effect.get("label", effect.get("id"))
                fx_id = skip.get("fixture_id")
                fx_label = skip.get("label") or fx_id
                if skip.get("partial"):
                    warnings.append(f"{label}: {fx_label} частково — не налаштовано {missing}")
                else:
                    warnings.append(f"{label}: {fx_label} не активовано — не налаштовано {missing}")
        return {
            "active": bool(active_ids),
            "active_ids": active_ids,
            "effects": effects,
            "warnings": warnings,
            "source_nonzero_channels": int(sum(1 for value in src if value)),
            "wire_nonzero_channels": int(sum(1 for value in wr if value)),
            "source_owner": self._source_owner(src),
        }

    def _editor_preview_output_blockers(self) -> list[str]:
        blockers: list[str] = []
        if self.engine.overlays.blackout:
            blockers.append("Епізод підготовлено, але Blackout блокує його вивід")
        if self.output.transport_kind.value != "artnet" or not self.output.allow_real_network:
            blockers.append("Art-Net вимкнено — фізичного виводу немає")
        if not self.output.udp_active:
            blockers.append("UDP вимкнено — фізичного виводу немає")
        if not self.output.armed:
            blockers.append("Disarm — фізичний вивід заблоковано")
        return blockers

    def _editor_preview_state(
        self,
        *,
        source: list[int] | None = None,
        wire: list[int] | None = None,
    ) -> dict[str, object] | None:
        if not self.engine.editor_preview_active:
            return {
                "active": False,
                "preset_id": None,
                "preset_label": None,
                "episode_index": None,
                "episode_id": None,
                "episode_title": None,
                "episode_duration_s": 0.0,
                "elapsed_s": 0.0,
                "restored_preset_id": None,
                "blockers": [],
                "source_nonzero_channels": 0,
                "wire_nonzero_channels": 0,
            }
        program = self.engine.editor_preview_program
        duration = float(getattr(program, "total_duration_s", 0.0) or 0.0)
        elapsed = float(self.engine.editor_preview_elapsed_s)
        cycle_elapsed = elapsed % duration if duration > 0 else 0.0
        src = source if source is not None else self._source_frame()
        wr = wire if wire is not None else self._wire_frame()
        meta = self._editor_preview_meta
        return {
            "active": True,
            "preset_id": meta.get("preset_id"),
            "preset_label": meta.get("preset_label"),
            "episode_index": meta.get("episode_index"),
            "episode_id": meta.get("episode_id"),
            "episode_title": meta.get("episode_title"),
            "episode_duration_s": duration,
            "elapsed_s": cycle_elapsed,
            "restored_preset_id": meta.get("restored_preset_id"),
            "blockers": self._editor_preview_output_blockers(),
            "source_nonzero_channels": int(sum(1 for value in src if value)),
            "wire_nonzero_channels": int(sum(1 for value in wr if value)),
        }

    def artnet_activation_blockers(self) -> list[str]:
        # Use wire frame so prepared Raw under Blackout does not block activation.
        frame = self._wire_frame()
        return self.output.activation_blockers(
            published_frame=frame,
            raw_tester_active=False,
        )

    def activate_artnet_network(
        self,
        *,
        confirmed: bool = False,
        allow_real_udp: bool = True,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        """Explicit confirmed Art-Net activation. Starts disarmed with zero frames.

        Production callers pass ``allow_real_udp=True``. Tests must pass
        ``allow_real_udp=False`` and inject a recording socket first.
        """
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if not confirmed:
            raise OutputError("Art-Net activation requires an explicit confirmation")
        from orng_led.desktop.output_role import local_artnet_allowed

        if self.output._injected_socket is None and not local_artnet_allowed():
            raise OutputError(
                "Локальний Art-Net вимкнено: для цієї локації відправляє Raspberry. "
                "Локальний вивід вмикається лише явним вибором."
            )

        # Keep Raw tester session intact — prepared source must survive activation.
        self.engine.set_blackout(True)
        # Ensure published frame is zero before the safety gate.
        self._publish_frame(self._published_frame())

        # Production: take target from YAML. Tests may pre-configure a loopback
        # target with an injected RecordingSocket — keep that address so venue
        # IP is never used in unit tests.
        if self.output._injected_socket is not None:
            if not self.output.target_ip:
                raise OutputError("Injected Art-Net socket requires a configured target_ip")
            self.output.configure_artnet(
                target_ip=self.output.target_ip,
                universe=self.output.universe,
                udp_port=self.output.udp_port,
                injected_socket=self.output._injected_socket,
            )
        elif self.show.app.artnet.target_ip:
            self.output.configure_artnet(
                target_ip=self.show.app.artnet.target_ip,
                universe=self.show.app.artnet.universe,
                udp_port=self.show.app.artnet.udp_port,
            )

        blockers = self.artnet_activation_blockers()
        if blockers:
            raise OutputError("Активацію Art-Net заблоковано: " + "; ".join(blockers))

        self.output.activate_artnet(
            explicit=True,
            confirmed=True,
            allow_real_udp=allow_real_udp,
        )
        # Venue Raspberry: operator has no Arm/Disarm — keep output open under blackout.
        self._ensure_venue_output_armed()
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def deactivate_artnet_network(
        self,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        """Send zeros, close UDP, return runtime to Mock + Blackout.

        Prepared Raw tester source is preserved across the return to Mock.
        """
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        self.output.deactivate_to_mock()
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def artnet_arm_blockers(self) -> list[str]:
        wire = self._wire_frame()
        return self.output.arm_blockers(wire_frame=wire)

    def arm_output(
        self,
        *,
        confirmed: bool = False,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        """Explicit confirmed Arm. Does not activate Art-Net or clear Blackout."""
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if not confirmed:
            raise OutputError("Arm requires confirmed=true")
        wire = self._wire_frame()
        self.output.arm(explicit=True, confirmed=True, wire_frame=wire)
        # Blackout stays on; publish confirms wire remains zeros.
        self._publish_frame(self._source_frame())
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def disarm_output(
        self,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        """Disarm: force Blackout + zeros; keep Art-Net UDP open if active.

        On venue Raspberry, immediately re-arm under blackout so the operator
        never needs an Arm control — BLACKOUT is the only light kill switch.
        """
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if self.beam_calibration.active:
            self.end_beam_calibration_test()
        self.output.disarm()
        self._ensure_venue_output_armed()
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def _venue_keep_output_armed(self) -> bool:
        from orng_led.raspberry.venue_ready import venue_ready_enabled

        return venue_ready_enabled()

    def _updater_blocks_venue_arm(self) -> bool:
        """Stay disarmed while external helper health-checks an apply/rollback.

        Managed Pi bootstrap helper lives outside the release tree and may still
        require blackout=true + output_armed=false. Venue auto-arm must wait
        until apply-request is cleared and update-state leaves the apply window;
        otherwise the helper times out and crash_recovery rolls back.
        """
        try:
            from orng_led.updater.paths import resolve_managed_paths
            from orng_led.updater.state import UpdateState, load_update_state

            paths = resolve_managed_paths()
            if (paths.app_root / "apply-request.json").is_file():
                return True
            record = load_update_state(paths)
            running = (
                os.environ.get("ORNG_BUILD_ID")
                or os.environ.get("ORNG_RELEASE_BUILD_ID")
                or ""
            ).strip()
            # Old helper wrote status=healthy and left state=restarting.
            # Once apply-request is gone and this process is the successful build,
            # do not hold venue arm forever.
            if (
                running
                and record.last_successful_build_id == running
                and not (paths.app_root / "apply-request.json").is_file()
            ):
                return False
            blocking = {
                UpdateState.APPLYING.value,
                UpdateState.RESTARTING.value,
                UpdateState.HEALTH_CHECK.value,
                UpdateState.ROLLING_BACK.value,
                UpdateState.WAITING_FOR_SAFE_STATE.value,
            }
            if str(record.state or "") in blocking:
                return True
            pending = record.pending_result if isinstance(record.pending_result, dict) else {}
            if str(pending.get("status") or "") in {"restarting", "applying", "health_check"}:
                return True
        except Exception:  # noqa: BLE001 — never block boot on updater probe errors
            return False
        return False

    def venue_ready_status(self) -> dict:
        from orng_led.raspberry.venue_ready import VenueReadyStatus, output_policy

        status = self._venue_status
        if isinstance(status, VenueReadyStatus):
            return status.to_dict()
        return VenueReadyStatus(policy=output_policy()).to_dict()

    def _venue_status_obj(self):
        from orng_led.raspberry.venue_ready import VenueReadyStatus, output_policy

        if self._venue_status is None:
            self._venue_status = VenueReadyStatus(policy=output_policy())
        return self._venue_status

    def _on_artnet_publish_failure(self, exc: Exception) -> None:
        """Art-Net send fault: force blackout; venue policy reopens under BO."""
        logger.warning("Art-Net publish failed: %s", exc)
        try:
            self.engine.set_blackout(True)
        except Exception as bo_exc:  # noqa: BLE001
            logger.warning("failed to force blackout after publish error: %s", bo_exc)
        if not self._venue_keep_output_armed():
            return
        status = self._venue_status_obj()
        status.state = "degraded"
        status.last_error = f"artnet_send_failed: {exc}"
        status.notes.append(status.last_error)
        self._nudge_venue_ready()

    def _nudge_venue_ready(self) -> None:
        evt = self._venue_ready_nudge
        if evt is not None and not evt.is_set():
            evt.set()

    def _start_venue_ready_supervisor(self) -> None:
        if not self._venue_keep_output_armed():
            return
        if self._venue_ready_task is not None and not self._venue_ready_task.done():
            self._nudge_venue_ready()
            return
        self._venue_ready_nudge = asyncio.Event()
        self._venue_ready_task = asyncio.create_task(
            self._venue_ready_loop(),
            name="orng-venue-ready",
        )

    async def _stop_venue_ready_supervisor(self) -> None:
        task = self._venue_ready_task
        self._venue_ready_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _venue_ready_loop(self) -> None:
        """Retry Art-Net + arm under blackout until ready or shutdown."""
        from orng_led.raspberry.venue_ready import (
            backoff_seconds,
            log_venue_event,
            output_policy,
            probe_network,
        )

        status = self._venue_status_obj()
        status.policy = output_policy()
        attempt = 0
        while self._running and not self._shutting_down:
            attempt += 1
            status.attempts = attempt
            try:
                target = getattr(self.show.app.artnet, "target_ip", None)
                probed = await asyncio.to_thread(probe_network, target=target)
                ok = self._venue_ready_attempt(status, probed=probed)
            except Exception as exc:  # noqa: BLE001
                ok = False
                status.last_error = f"venue_attempt_error: {exc}"
                status.state = "degraded"
                log_venue_event(logging.WARNING, "attempt %s failed: %s", attempt, exc)
            if ok:
                status.state = "ready"
                status.last_error = None
                log_venue_event(logging.INFO, "ready (attempt %s)", attempt)
                # Sleep until nudged (send fault / scene swap) or shutdown.
                nudge = self._venue_ready_nudge
                if nudge is None:
                    return
                nudge.clear()
                try:
                    await nudge.wait()
                except asyncio.CancelledError:
                    raise
                attempt = 0
                continue
            delay = backoff_seconds(attempt)
            link, route, reason = await asyncio.to_thread(
                probe_network,
                target=getattr(self.show.app.artnet, "target_ip", None),
            )
            status.ethernet_link = link
            status.artnet_route_valid = route
            if reason and not status.last_error:
                status.last_error = reason
            status.state = "waiting_network" if reason else "activating"
            log_venue_event(
                logging.WARNING,
                "not ready (attempt %s): %s; retry in %.1fs",
                attempt,
                status.last_error or "unknown",
                delay,
            )
            nudge = self._venue_ready_nudge
            if nudge is not None:
                nudge.clear()
            try:
                await asyncio.wait_for(
                    nudge.wait() if nudge is not None else asyncio.sleep(delay),
                    timeout=delay,
                )
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                raise

    def _venue_ready_attempt(
        self,
        status,
        probed: tuple[bool | None, bool | None, str | None] | None = None,
    ) -> bool:
        """One readiness pass. Returns True when Art-Net+armed+blackout."""
        from orng_led.output.contract import TransportKind
        from orng_led.raspberry.venue_ready import probe_network, resolve_startup_preset

        if self.scene_apply_in_progress:
            status.state = "activating"
            status.last_error = "scene_apply"
            return False

        status.state = "activating"
        if probed is None:
            link, route, reason = probe_network(
                target=getattr(self.show.app.artnet, "target_ip", None)
            )
        else:
            link, route, reason = probed
        status.ethernet_link = link
        status.artnet_route_valid = route
        # On real Raspberry, refuse activate until eth/route look valid.
        # In unit tests (injected socket / non-Linux) probes are None → proceed.
        if reason is not None and link is not None and route is not None:
            status.last_error = reason
            status.state = "waiting_network"
            return False

        # Startup preset under blackout so clear-BO shows light immediately.
        preset_id = resolve_startup_preset(self.show.app)
        status.startup_preset = preset_id
        if not bool(self.engine.overlays.blackout):
            self.engine.set_blackout(True)
        try:
            if preset_id in self.engine.presets or preset_id == "A01":
                if preset_id not in self.engine.presets:
                    # refresh may have dropped it; leave current
                    pass
                elif self.engine.active_preset_id != preset_id:
                    self.engine.select_preset(preset_id, reset_clock=True)
        except Exception as exc:  # noqa: BLE001
            status.last_error = f"startup_preset_failed: {exc}"
            logger.warning("venue_ready startup preset %s failed: %s", preset_id, exc)

        try:
            if self.output.transport_kind is not TransportKind.ARTNET or not self.output.udp_active:
                self.activate_artnet_network(confirmed=True, allow_real_udp=True)
            else:
                self._ensure_venue_output_armed()
        except Exception as exc:  # noqa: BLE001
            status.last_error = f"activate_failed: {exc}"
            logger.warning("venue_ready activate failed: %s", exc)
            return False

        out = self.output.status()
        ready = (
            out.transport.value == "artnet"
            and bool(out.udp_active)
            and bool(out.armed)
            and bool(self.engine.overlays.blackout)
        )
        if not ready:
            status.last_error = (
                f"not_ready transport={out.transport.value} udp={out.udp_active} "
                f"armed={out.armed} blackout={self.engine.overlays.blackout}"
            )
            return False
        return True

    def _ensure_venue_output_armed(self) -> None:
        """Arm under blackout when venue policy requires always-open output.

        Never clears blackout — BLACKOUT is the only operator light kill switch.
        Deferred while updater helper is health-checking (see _updater_blocks_venue_arm).
        """
        if not self._venue_keep_output_armed():
            return
        if self.scene_apply_in_progress:
            status = self._venue_status_obj()
            status.state = "activating"
            status.last_error = "scene_apply"
            return
        if self._updater_blocks_venue_arm():
            status = self._venue_status_obj()
            status.state = "activating"
            status.last_error = "updater_health_window"
            return
        if self.output.armed:
            return
        # Arm gate requires blackout + dark light channels on the wire frame.
        if not bool(self.engine.overlays.blackout):
            self.engine.set_blackout(True)
            self._publish_frame(self._published_frame())
        try:
            wire = self._wire_frame()
            self.output.arm(explicit=True, confirmed=True, wire_frame=wire)
            self._publish_frame(self._source_frame())
        except Exception as exc:  # noqa: BLE001 — leave disarmed if gate still blocks
            logger.warning("venue arm under blackout failed: %s", exc)
            status = self._venue_status_obj()
            status.last_error = f"arm_failed: {exc}"
            return

    def _ensure_venue_show_ready(self) -> None:
        """Kick venue readiness (sync one-shot + nudge supervisor).

        Prefer the async supervisor; this sync path remains for scene-swap /
        activate/disarm callers when the event loop task is not yet running.
        """
        if not self._venue_keep_output_armed():
            return
        status = self._venue_status_obj()
        try:
            self._venue_ready_attempt(status)
        except Exception as exc:  # noqa: BLE001 — never block scene apply
            logger.warning("venue_ready sync attempt failed: %s", exc)
            status.last_error = f"sync_attempt_failed: {exc}"
        self._nudge_venue_ready()
        if self._running and (
            self._venue_ready_task is None or self._venue_ready_task.done()
        ):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                self._start_venue_ready_supervisor()

    def apply_preview_speed(
        self,
        value: float,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        from orng_led.input.contract import PROGRAM_SPEED_MIN

        if self._tempo_holds_speed():
            state = self.build_state()
            self._remember(client_command_id, state)
            return state, False
        self.preview_speed = max(PROGRAM_SPEED_MIN, min(120.0, float(value)))
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_next_auto_look(
        self,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if not self._manual_show_locked():
            raise ValueError("Кнопка лише в Auto")
        self.bpm.operator_next_look()
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_bpm_sync(
        self,
        *,
        enabled: bool | None = None,
        mode: str | None = None,
        scope: str | None = None,
        palette: str | None = None,
        cast: str | None = None,
        groups: list[str] | None = None,
        excluded: list[str] | None = None,
        character: str | None = None,
        intensity: str | None = None,
        period: float | str | None = None,
        par_rhythm: bool | None = None,
        accent: float | None = None,
        client_command_id: str | None = None,
        source_id: str | None = None,
        intent_ms: int | None = None,
    ) -> tuple[AppStateResponse, bool]:
        fingerprint = self._command_fingerprint(
            "bpm_sync",
            enabled=enabled,
            mode=mode,
            scope=scope,
            palette=palette,
            cast=cast,
            groups=groups,
            excluded=excluded,
            character=character,
            intensity=intensity,
            period=period,
            par_rhythm=par_rhythm,
            accent=accent,
        )
        cached = self._idempotent(client_command_id, fingerprint)
        if cached is not None:
            return cached, True
        if not self.bpm.accept_intent(source_id, intent_ms):
            return self.build_state(), True
        was_auto = bool(self.bpm.enabled and self.bpm.scope == "auto")
        self.bpm.configure(
            enabled=enabled,
            mode=mode,
            scope=scope,
            palette=palette,
            cast=cast,
            groups=groups,
            excluded=excluded,
            character=character,
            intensity=intensity,
            period=period,
            par_rhythm=par_rhythm,
            accent=accent,
        )
        now_auto = bool(self.bpm.enabled and self.bpm.scope == "auto")
        if now_auto and not was_auto:
            self._release_manual_for_auto()
        if not now_auto:
            self.engine.auto_groups = None
            self.engine.auto_base = None
        elif self.bpm.groups is not None:
            self.engine.auto_groups = tuple(self.bpm.groups)
        self._track_manual_speed()
        state = self.build_state()
        self._remember(client_command_id, fingerprint)
        return state, False

    def _manual_show_locked(self) -> bool:
        return bool(self.bpm.enabled and self.bpm.scope == "auto")

    def _remember_desk(self, frame: list[int]) -> None:
        """Keep the newest picture. The copy is the universe, not a state dump."""
        self._desk_frame = list(frame)
        self._desk_at = time.monotonic()
        self._desk_sequence += 1
        current = self.bpm.planner.current
        clock = None if current is None else current.clock
        self._desk_program = "beat" if clock == "musical" else "smooth"
        self._desk_look = None if current is None else str(current.look)

    def desk_preview(self) -> dict[str, object] | None:
        """The effect that is playing. Not a picture of the fixtures.

        ``None`` until a frame has been published, so a stopped renderer
        keeps one sequence and the age grows.
        """
        frame = self._desk_frame
        captured = self._desk_at
        if frame is None or captured is None:
            return None
        age_ms = max(0.0, (time.monotonic() - float(captured)) * 1000.0)
        return {
            "session": self._desk_session,
            "sequence": int(self._desk_sequence),
            "age_ms": round(age_ms, 1),
            "program": self._desk_program,
            "look": self._desk_look,
            "preset_id": self.engine.active_preset_id,
        }

    def _control_preview_view(self, wire: list[int]):
        """Auto desk shows the playing look, not the gated Art-Net wire."""
        if not self._manual_show_locked():
            return decode_simulator_view(self.show, wire)
        base = self.engine.auto_base
        if base is None:
            return decode_simulator_view(self.show, wire)
        from orng_led.engine.renderer import render_stage

        frame = render_stage(self.show, base, self.engine.beam_motion, master=1.0)
        view = decode_simulator_view(self.show, frame)
        return view.model_copy(
            update={"blackout_visual": bool(self.engine.overlays.blackout)}
        )

    def _release_manual_for_auto(self) -> None:
        """Drop the manual drop overlay. Strobe, sweeps and Face stay on top."""
        self.engine.drop_release()

    def _manual_rejected(
        self, client_command_id: str | None
    ) -> tuple[AppStateResponse, bool]:
        del client_command_id
        raise ManualShowLockedError("auto_show_locked")

    def enter_raw_tester(self) -> AppStateResponse:
        """Start Raw as prepared source. Does not activate Art-Net, Arm, or clear Blackout."""
        if self.beam_calibration.active:
            self.end_beam_calibration_test()
        self.raw_tester.enter()
        self._publish_frame(self._source_frame())
        self.sequence += 1
        return self.build_state()

    def exit_raw_tester(self) -> AppStateResponse:
        """Explicit Raw exit: Blackout + zero frames first, then clear the source."""
        from orng_led.engine.frame import empty_frame
        from orng_led.output.contract import TransportKind

        self.engine.set_blackout(True)
        if self.output.transport_kind is TransportKind.ARTNET and self.output.udp_active:
            self.output._emit_zero_frames(count=3, raise_on_error=False)
        else:
            self._publish_frame(empty_frame())
        self.raw_tester.exit()
        self._publish_frame(empty_frame())
        self.sequence += 1
        return self.build_state()

    def raw_tester_blackout(self) -> AppStateResponse:
        if not self.raw_tester.active:
            self.enter_raw_tester()
        zeros = self.raw_tester.blackout()
        self._publish_frame(zeros if not self.engine.overlays.blackout else self._published_frame())
        self.sequence += 1
        return self.build_state()

    def raw_tester_set(
        self,
        *,
        channel: int | None = None,
        value: int | None = None,
        channels: dict[int, int] | None = None,
    ) -> AppStateResponse:
        if not self.raw_tester.active:
            raise RuntimeError("Raw tester is not active")
        if channels:
            self.raw_tester.set_channels(channels)
        elif channel is not None and value is not None:
            self.raw_tester.set_channel(channel, value)
        else:
            raise ValueError("Provide channel+value or channels map")
        self._publish_frame(self._published_frame())
        self.sequence += 1
        return self.build_state()

    def _identify_channels(self, fixture, profile, level: int) -> dict[int, int]:
        """Provisional Mock-visible identify values (never hardware_verified)."""
        channels: dict[int, int] = {}
        level = max(0, min(255, level))

        def set_role(role: ChannelRole, value: int) -> None:
            for ch in profile.channels:
                if ch.role is role:
                    channels[global_channel(fixture.start_address, ch.local)] = value

        # Prefer cyan when RGB roles exist. If only dimmer+red are mapped
        # (hardware-confirmed PAR), raise those so identify stays Mock-visible.
        set_role(ChannelRole.DIMMER, level)
        has_green = any(ch.role is ChannelRole.GREEN for ch in profile.channels)
        has_blue = any(ch.role is ChannelRole.BLUE for ch in profile.channels)
        if has_green and has_blue:
            set_role(ChannelRole.RED, 0)
            set_role(ChannelRole.GREEN, level)
            set_role(ChannelRole.BLUE, level)
        else:
            set_role(ChannelRole.RED, level)
        if fixture.kind in (FixtureKind.PAR, FixtureKind.FACE_PAR):
            set_role(ChannelRole.WHITE, 0)
        elif fixture.kind is FixtureKind.BAR:
            for ch in profile.channels:
                if ch.role is ChannelRole.SEGMENT:
                    channels[global_channel(fixture.start_address, ch.local)] = level
                elif ch.role in (ChannelRole.SEGMENT_COLOR, ChannelRole.WHOLE_COLOR):
                    table = ch.palette or {}
                    channels[global_channel(fixture.start_address, ch.local)] = int(
                        table.get("cyan", table.get("blue", 48))
                    )
        elif fixture.kind is FixtureKind.BEAM:
            set_role(ChannelRole.SHUTTER, 255)
            set_role(ChannelRole.COLOR, 0)
            # Controlled provisional home pose for Mock visibility.
            set_role(ChannelRole.PAN_COARSE, 128)
            set_role(ChannelRole.TILT_COARSE, 128)
        return channels

    def begin_fixture_channel_test(self, fixture_id: str) -> AppStateResponse:
        """Start Raw-backed single-fixture channel testing without touching Art-Net/Arm."""
        if self.beam_calibration.active:
            self.end_beam_calibration_test()
        fixture = next((fx for fx in self.show.patch.fixtures if fx.id == fixture_id), None)
        if fixture is None:
            raise KeyError(f"Unknown fixture {fixture_id!r}")
        if not self.raw_tester.active:
            self.raw_tester.enter()
        else:
            self.raw_tester.blackout()
        self._publish_frame(self.raw_tester.frame, from_raw=True)
        self.sequence += 1
        return self.build_state()

    def set_fixture_local_channel(
        self,
        fixture_id: str,
        local: int,
        value: int,
    ) -> AppStateResponse:
        fixture = next((fx for fx in self.show.patch.fixtures if fx.id == fixture_id), None)
        if fixture is None:
            raise KeyError(f"Unknown fixture {fixture_id!r}")
        profile = self.show.profile_for(fixture)
        if local < 1 or local > profile.footprint:
            raise ValueError(f"Local channel {local} outside footprint {profile.footprint}")
        if not self.raw_tester.active:
            self.begin_fixture_channel_test(fixture_id)
        global_channel = fixture.start_address + local - 1
        self.raw_tester.set_channel(global_channel, value)
        self._publish_frame(self.raw_tester.frame, from_raw=True)
        self.sequence += 1
        return self.build_state()

    def reset_fixture_channel_test(self, fixture_id: str) -> AppStateResponse:
        fixture = next((fx for fx in self.show.patch.fixtures if fx.id == fixture_id), None)
        if fixture is None:
            raise KeyError(f"Unknown fixture {fixture_id!r}")
        if not self.raw_tester.active:
            self.begin_fixture_channel_test(fixture_id)
        profile = self.show.profile_for(fixture)
        for local in range(1, profile.footprint + 1):
            self.raw_tester.set_channel(fixture.start_address + local - 1, 0)
        self._publish_frame(self.raw_tester.frame, from_raw=True)
        self.sequence += 1
        return self.build_state()

    def end_fixture_channel_test(self) -> AppStateResponse:
        return self.exit_raw_tester()

    def _require_calibration_session(
        self,
        *,
        session_id: str | None = None,
        owner: str | None = None,
    ) -> None:
        session = self.beam_calibration
        if not session.active or not session.session_id:
            raise ValueError("Калібрування Beam не активне")
        if session_id and session_id != session.session_id:
            raise ValueError("Сесія калібрування завершена")
        if owner and session.owner and owner != session.owner:
            raise ValueError("Калібрування належить іншій сесії")

    def begin_beam_calibration_test(
        self,
        fixture_id: str,
        *,
        confirmed: bool = False,
        owner: str | None = None,
    ) -> AppStateResponse:
        from orng_led.setup.beam_calibration import begin_blockers

        if not confirmed:
            raise ValueError(
                "Початок калібрування Beam потребує явного підтвердження "
                "(невідомий напрямок Pan/Tilt може викликати рух)"
            )
        blockers = begin_blockers(self.show, fixture_id)
        if blockers:
            raise ValueError(blockers[0])
        if self.beam_calibration.active and self.beam_calibration.fixture_id != fixture_id:
            raise ValueError(
                "Вже активне калібрування іншої голови — спочатку зупиніть поточний тест"
            )
        if self.raw_tester.active:
            self.exit_raw_tester()
        fixture = next(fx for fx in self.show.patch.fixtures if fx.id == fixture_id)
        # Start with a fully zeroed footprint — no auto home / sweep / light.
        from orng_led.engine.frame import empty_frame
        import uuid

        self.beam_calibration = BeamCalibrationTestSession(
            active=True,
            fixture_id=fixture_id,
            session_id=str(uuid.uuid4()),
            owner=owner,
            semantic_pan=float(fixture.spatial.home_pan),
            semantic_tilt=float(fixture.spatial.home_tilt),
            frame=empty_frame(),
        )
        self._publish_frame(self._source_frame(), from_raw=False)
        self.sequence += 1
        return self.build_state()

    def set_beam_calibration_position(
        self,
        *,
        pan: float | None = None,
        tilt: float | None = None,
        session_id: str | None = None,
        owner: str | None = None,
    ) -> AppStateResponse:
        from orng_led.engine.beam_transform import clamp01

        self._require_calibration_session(session_id=session_id, owner=owner)
        if pan is not None:
            self.beam_calibration.semantic_pan = clamp01(pan)
        if tilt is not None:
            self.beam_calibration.semantic_tilt = clamp01(tilt)
        self._publish_frame(self._source_frame(), from_raw=False)
        self.sequence += 1
        return self.build_state()

    def set_beam_calibration_visible(
        self,
        *,
        enabled: bool,
        confirmed: bool = False,
        session_id: str | None = None,
        owner: str | None = None,
    ) -> AppStateResponse:
        from orng_led.setup.beam_calibration import visible_beam_blockers

        self._require_calibration_session(session_id=session_id, owner=owner)
        if not self.beam_calibration.fixture_id:
            raise ValueError("Калібрування Beam не активне")
        fixture = next(
            fx for fx in self.show.patch.fixtures if fx.id == self.beam_calibration.fixture_id
        )
        if enabled:
            blockers = visible_beam_blockers(
                self.show, fixture, blackout=self.engine.overlays.blackout
            )
            if blockers:
                raise ValueError(blockers[0])
            if not confirmed:
                raise ValueError(
                    "Видимий промінь потребує окремого підтвердження (мінімальна яскравість)"
                )
            self.beam_calibration.visible_beam_requested = True
            self.beam_calibration.visible_beam_confirmed = True
        else:
            self.beam_calibration.visible_beam_requested = False
            self.beam_calibration.visible_beam_confirmed = False
        self._publish_frame(self._source_frame(), from_raw=False)
        self.sequence += 1
        return self.build_state()

    def beam_calibration_go_home(
        self,
        *,
        session_id: str | None = None,
        owner: str | None = None,
    ) -> AppStateResponse:
        self._require_calibration_session(session_id=session_id, owner=owner)
        if not self.beam_calibration.fixture_id:
            raise ValueError("Калібрування Beam не активне")
        fixture = next(
            fx for fx in self.show.patch.fixtures if fx.id == self.beam_calibration.fixture_id
        )
        return self.set_beam_calibration_position(
            pan=float(fixture.spatial.home_pan),
            tilt=float(fixture.spatial.home_tilt),
        )

    def end_beam_calibration_test(self) -> AppStateResponse:
        fixture_id = self.beam_calibration.fixture_id
        session_pan = float(self.beam_calibration.semantic_pan)
        session_tilt = float(self.beam_calibration.semantic_tilt)
        # End session without publishing Pan/Tilt zeros and without auto-home.
        self.beam_calibration = BeamCalibrationTestSession()
        if fixture_id:
            from orng_led.engine.beam import BeamMotionState

            fixture = next((fx for fx in self.show.patch.fixtures if fx.id == fixture_id), None)
            if fixture is not None:
                # Keep the last calibration pose as last_valid — do not glide home.
                self.engine.beam_motion[fixture_id] = BeamMotionState(
                    pan=session_pan,
                    tilt=session_tilt,
                    last_valid_pan=session_pan,
                    last_valid_tilt=session_tilt,
                )
        self._publish_frame(self._published_frame(), from_raw=False)
        self.sequence += 1
        return self.build_state()

    def save_beam_calibration(self, fixture_id: str, spatial_update: dict) -> AppStateResponse:
        """Persist per-head Beam calibration into patch.yaml and hot-reload."""
        from orng_led.config.models import FixtureKind, SpatialPlacement
        from orng_led.config.validation import global_channel
        from orng_led.engine.frame import empty_frame

        fixture = next((fx for fx in self.show.patch.fixtures if fx.id == fixture_id), None)
        if fixture is None or fixture.kind is not FixtureKind.BEAM:
            raise KeyError(f"Unknown beam fixture {fixture_id!r}")
        # Clear old footprint before applying new transform.
        profile = self.show.profile_for(fixture)
        clear = empty_frame()
        for local in range(1, profile.footprint + 1):
            clear[global_channel(fixture.start_address, local) - 1] = 0
        self._publish_frame(clear, from_raw=False)

        current = fixture.spatial.model_dump(mode="json")
        current.update(spatial_update)
        new_spatial = SpatialPlacement.model_validate(current)
        fixtures = []
        for fx in self.show.patch.fixtures:
            if fx.id == fixture_id:
                fixtures.append(fx.model_copy(update={"spatial": new_spatial}))
            else:
                fixtures.append(fx)
        patch = self.show.patch.model_copy(update={"fixtures": fixtures})
        return self.save_patch(patch.model_dump(mode="json"), reset_beam_motion_id=fixture_id)

    def channel_role_catalog(self) -> list[dict[str, str]]:
        from orng_led.config.models import ChannelRole

        labels = {
            ChannelRole.UNUSED: "Не використовується / завжди 0",
            ChannelRole.DIMMER: "Master Dimmer",
            ChannelRole.RED: "Red",
            ChannelRole.GREEN: "Green",
            ChannelRole.BLUE: "Blue",
            ChannelRole.WHITE: "White",
            ChannelRole.AMBER: "Amber",
            ChannelRole.UV: "UV",
            ChannelRole.STROBE: "Strobe",
            ChannelRole.STROBE_SPEED: "Strobe Speed",
            ChannelRole.PROGRAM: "Program / Effect",
            ChannelRole.EFFECT_SPEED: "Effect Speed",
            ChannelRole.DIRECTION_MODE: "Direction / Mode",
            ChannelRole.WHOLE_COLOR: "Whole Fixture Color / Palette",
            ChannelRole.SEGMENT_COLOR: "Segment Color",
            ChannelRole.SEGMENT: "Segment Level",
            ChannelRole.PAN_COARSE: "Pan",
            ChannelRole.PAN_FINE: "Pan Fine",
            ChannelRole.TILT_COARSE: "Tilt",
            ChannelRole.TILT_FINE: "Tilt Fine",
            ChannelRole.MOVEMENT_SPEED: "Movement Speed",
            ChannelRole.COLOR: "Color Wheel",
            ChannelRole.GOBO: "Gobo",
            ChannelRole.GOBO_ROTATION: "Gobo Rotation",
            ChannelRole.PRISM: "Prism",
            ChannelRole.PRISM_ROTATION: "Prism Rotation",
            ChannelRole.FOCUS: "Focus",
            ChannelRole.ZOOM: "Zoom",
            ChannelRole.RESET: "Reset",
            ChannelRole.FIXED: "Фіксоване значення",
            ChannelRole.SHUTTER: "Shutter",
            ChannelRole.MACRO: "Program / Effect (macro)",
            ChannelRole.SPEED: "Effect Speed (legacy)",
            ChannelRole.UNKNOWN: "Не використовується / завжди 0",
        }
        order = [
            ChannelRole.UNUSED,
            ChannelRole.DIMMER,
            ChannelRole.RED,
            ChannelRole.GREEN,
            ChannelRole.BLUE,
            ChannelRole.WHITE,
            ChannelRole.AMBER,
            ChannelRole.UV,
            ChannelRole.STROBE,
            ChannelRole.STROBE_SPEED,
            ChannelRole.PROGRAM,
            ChannelRole.EFFECT_SPEED,
            ChannelRole.DIRECTION_MODE,
            ChannelRole.WHOLE_COLOR,
            ChannelRole.SEGMENT_COLOR,
            ChannelRole.SEGMENT,
            ChannelRole.PAN_COARSE,
            ChannelRole.PAN_FINE,
            ChannelRole.TILT_COARSE,
            ChannelRole.TILT_FINE,
            ChannelRole.MOVEMENT_SPEED,
            ChannelRole.COLOR,
            ChannelRole.GOBO,
            ChannelRole.GOBO_ROTATION,
            ChannelRole.PRISM,
            ChannelRole.PRISM_ROTATION,
            ChannelRole.FOCUS,
            ChannelRole.ZOOM,
            ChannelRole.RESET,
            ChannelRole.FIXED,
            ChannelRole.SHUTTER,
        ]
        return [{"role": role.value, "label": labels[role]} for role in order]

    def identify_fixture(self, fixture_id: str, level: int = 200) -> AppStateResponse:
        fixture = next((fx for fx in self.show.patch.fixtures if fx.id == fixture_id), None)
        if fixture is None:
            raise KeyError(f"Unknown fixture {fixture_id!r}")
        profile = self.show.profile_for(fixture)
        if not self.raw_tester.active:
            self.enter_raw_tester()
        else:
            self.raw_tester.blackout()
        values = self._identify_channels(fixture, profile, level)
        if not values:
            raise ConfigError(f"Fixture {fixture_id!r} has no channels for identify")
        self.raw_tester.set_channels(values)
        self._publish_frame(self._published_frame())
        self.sequence += 1
        return self.build_state()

    def identify_group(self, group: str, level: int = 180) -> AppStateResponse:
        matches = [fx for fx in self.show.patch.fixtures if group in fx.groups]
        if not matches:
            raise KeyError(f"No fixtures in group {group!r}")
        if not self.raw_tester.active:
            self.enter_raw_tester()
        else:
            self.raw_tester.blackout()
        values: dict[int, int] = {}
        for fixture in matches:
            profile = self.show.profile_for(fixture)
            values.update(self._identify_channels(fixture, profile, level))
        self.raw_tester.set_channels(values)
        self._publish_frame(self._published_frame())
        self.sequence += 1
        return self.build_state()

    def validate_patch_payload(
        self,
        patch_data: dict,
        profiles_data: dict | None = None,
    ) -> dict:
        profiles = self.show.profiles
        if profiles_data:
            profiles = {
                pid: parse_model(FixtureProfile, pdata, source=f"profiles[{pid}]")
                for pid, pdata in profiles_data.items()
            }
        patch = parse_model(PatchDocument, patch_data, source="patch")
        errors = collect_patch_errors(patch, profiles)
        return {"ok": not errors, "errors": errors}

    def save_app_config(self, data: dict) -> AppStateResponse:
        app = parse_model(AppConfig, data, source="app.yaml")
        # HOME safety: never persist armed Art-Net or fake hardware verification.
        app = app.model_copy(
            update={
                "output_armed": False,
                "artnet": app.artnet.model_copy(update={"hardware_verified": False}),
            }
        )
        if app.transport is TransportMode.ARTNET and not app.artnet.target_ip:
            # Allow selecting Art-Net mode in YAML draft only with IP filled later;
            # keep preferred transport but do not enable real output.
            pass
        save_model(self.config_dir / "app.yaml", app)
        self.show = self.show.model_copy(update={"app": app})
        self.engine.show = self.show
        self.engine.overlays.master_brightness = app.master_brightness
        self.engine.set_strobe_speed(app.strobe_speed)
        self.engine.set_sweep_speed(app.sweep_speed)
        # Configure Art-Net settings in memory but stay on Mock unless already forced.
        if app.artnet.target_ip:
            self.output.configure_artnet(
                target_ip=app.artnet.target_ip,
                universe=app.artnet.universe,
                udp_port=app.artnet.udp_port,
            )
        # Never auto-arm; keep Mock publishing during HOME wizard.
        self.output.use_mock()
        return self.build_state()

    def save_patch(
        self,
        data: dict,
        *,
        reset_beam_motion_id: str | None = None,
    ) -> AppStateResponse:
        patch = parse_model(PatchDocument, data, source="patch.yaml")
        errors = collect_patch_errors(patch, self.show.profiles)
        if errors:
            raise ConfigError("Patch validation failed:\n- " + "\n- ".join(errors))
        layout_ids = set(self.show.layout.fixtures)
        patch_ids = {fx.id for fx in patch.fixtures}
        if layout_ids != patch_ids:
            raise ConfigError(
                "layout.fixtures must list exactly the patch fixture ids. "
                f"missing={sorted(patch_ids - layout_ids)}, "
                f"extra={sorted(layout_ids - patch_ids)}."
            )
        save_model(self.config_dir / "patch.yaml", patch)
        self.show = self.show.model_copy(update={"patch": patch})
        self.engine.show = self.show
        if reset_beam_motion_id:
            self.engine.reset_beam_motion_to_home(reset_beam_motion_id)
        self._request_head_travel()
        self._publish_frame(self._published_frame())
        self.sequence += 1
        return self.build_state()

    def save_profile(self, data: dict) -> AppStateResponse:
        profile = parse_model(FixtureProfile, data, source="profile")
        profile = profile.model_copy(update={"hardware_verified": False})
        path = self.config_dir / "profiles" / f"{profile.id}.yaml"
        save_model(path, profile)
        profiles = dict(self.show.profiles)
        profiles[profile.id] = profile
        self.show = self.show.model_copy(update={"profiles": profiles})
        self.engine.show = self.show
        self._request_head_travel()
        # Drop any stale DMX on old locals, then re-render active intents.
        self._publish_frame(self._published_frame())
        self.sequence += 1
        return self.build_state()

    def save_layout(self, data: dict) -> AppStateResponse:
        layout = parse_model(SpatialLayout, data, source="layout.yaml")
        patch_ids = {fx.id for fx in self.show.patch.fixtures}
        if set(layout.fixtures) != patch_ids:
            raise ConfigError(
                "layout.fixtures must list exactly the patch fixture ids. "
                f"missing={sorted(patch_ids - set(layout.fixtures))}, "
                f"extra={sorted(set(layout.fixtures) - patch_ids)}."
            )
        save_model(self.config_dir / "layout.yaml", layout)
        self.show = self.show.model_copy(update={"layout": layout})
        self.engine.show = self.show
        return self.build_state()

    def reload_from_disk(self) -> AppStateResponse:
        if self.beam_calibration.active:
            self.end_beam_calibration_test()
        if self.raw_tester.active:
            self.exit_raw_tester()
        loaded = load_show_config(self.config_dir)
        self.show = loaded
        self.engine.show = loaded
        self._request_head_travel()
        self.engine.overlays.master_brightness = loaded.app.master_brightness
        self.output.use_mock()
        if loaded.app.artnet.target_ip:
            self.output.configure_artnet(
                target_ip=loaded.app.artnet.target_ip,
                universe=loaded.app.artnet.universe,
                udp_port=loaded.app.artnet.udp_port,
            )
        return self.build_state()

    def readiness_summary(self) -> dict:
        profiles = [
            {
                "id": profile.id,
                "label": profile.label,
                "hardware_verified": False,
                "badge": "Не перевірено на обладнанні",
            }
            for profile in self.show.profiles.values()
        ]
        out = self.output.status()
        source = self._source_frame()
        wire = self.output.wire_frame(source)
        return {
            "transport_preferred": self.show.app.transport.value,
            "runtime_transport": self.output.transport_kind.value,
            "output_armed": out.armed,
            "artnet_network_enabled": out.network_allowed,
            "udp_active": out.udp_active,
            "blackout": self.engine.overlays.blackout,
            "frame_sum": int(sum(wire)),
            "nonzero_channels": int(sum(1 for value in wire if value)),
            "source_frame_sum": int(sum(source)),
            "source_nonzero_channels": int(sum(1 for value in source if value)),
            "wire_frame_sum": int(sum(wire)),
            "wire_nonzero_channels": int(sum(1 for value in wire if value)),
            "source_owner": self._source_owner(source),
            "activation_blockers": self.artnet_activation_blockers(),
            "arm_blockers": self.artnet_arm_blockers(),
            "artnet_hardware_verified": False,
            "artnet_badge": "Не перевірено на обладнанні",
            "profiles": profiles,
            "fixture_count": len(self.show.patch.fixtures),
            "patch_ok": len(collect_patch_errors(self.show.patch, self.show.profiles)) == 0,
            "raw_tester_active": self.raw_tester.active,
            "notes": [
                "Збережений Art-Net у YAML не активує мережу автоматично.",
                "Реальний UDP починається лише після явного підтвердження кнопкою "
                "«Безпечно активувати Art-Net».",
                "Підготовлений Raw-кадр зберігається між кроками майстра і під час "
                "активації Art-Net / Arm.",
                "Arm вмикається окремо кнопкою «Увімкнути Arm» лише при Blackout "
                "і нульовому wire-кадрі.",
                "Поки armed=false або Blackout=true, на дріт ідуть лише нулі.",
            ],
        }

    def _command_fingerprint(self, name: str, **fields: object) -> str:
        items = ",".join(f"{key}={fields[key]!r}" for key in sorted(fields))
        return f"{name}:{items}"

    def _remember(self, client_command_id: str | None, fingerprint: object = "") -> None:
        if not client_command_id:
            return
        mark = fingerprint if isinstance(fingerprint, str) and fingerprint else "*"
        self._seen_commands[client_command_id] = mark
        self._seen_commands.move_to_end(client_command_id)
        while len(self._seen_commands) > self.max_idempotency_keys:
            self._seen_commands.popitem(last=False)

    def _idempotent(
        self,
        client_command_id: str | None,
        fingerprint: str | None = None,
    ) -> AppStateResponse | None:
        if not client_command_id or client_command_id not in self._seen_commands:
            return None
        seen = self._seen_commands[client_command_id]
        if fingerprint is not None and seen != "*" and seen != fingerprint:
            raise CommandConflictError("command_id reused with different content")
        return self.build_state()

    def apply_select_preset(
        self,
        preset_id: str,
        *,
        reset_clock: bool = True,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        from orng_led.engine.presets import NONE_PRESET_ID, NonePresetProgram

        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if self._manual_show_locked():
            return self._manual_rejected(client_command_id)
        # Choosing a pad preset ends any editor hardware preview without restoring.
        if self.engine.editor_preview_active:
            self._clear_editor_preview(restore=False)
        if preset_id == NONE_PRESET_ID:
            self.engine.presets[NONE_PRESET_ID] = NonePresetProgram()
        self.engine.select_preset(preset_id, reset_clock=reset_clock)
        self.bpm.drop_bar_plan()
        # New look always starts at ×1 intensity (encoder speed does not carry over).
        self._manual_speed = None
        self.preview_speed = 1.0
        self.engine.effect_rate_scale = 1.0
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_seek_episode(
        self,
        episode_index: int,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if self._manual_show_locked():
            return self._manual_rejected(client_command_id)
        from orng_led.engine.presets import NONE_PRESET_ID

        # NONE has no episodes. The zoom encoder must not look it up or log an error.
        if self.engine.active_preset_id == NONE_PRESET_ID:
            state = self.build_state()
            self._remember(client_command_id, state)
            return state, False
        self.bpm.drop_bar_plan()
        self._musical_pending = None
        self._musical_manual_at = float(self.bpm.section.clock_s)
        self.engine.seek_episode(episode_index)
        # Publish immediately so the new episode is on the wire/source now.
        self._publish_frame(self._published_frame())
        self.sequence += 1
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_white_hit(
        self, *, client_command_id: str | None = None
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if self._manual_show_locked():
            return self._manual_rejected(client_command_id)
        self.engine.trigger_white_hit()
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_strobe(
        self,
        action: str,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if action == "press":
            self.engine.strobe_press()
        elif action == "release":
            self.engine.strobe_release()
        else:
            raise ValueError(f"Unknown strobe action: {action}")
        # Publish immediately (same as sweep) so hold edges are not one tick late.
        self._publish_frame(self._published_frame())
        self.sequence += 1
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_drop(
        self,
        action: str,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if action == "press":
            if self._manual_show_locked():
                return self._manual_rejected(client_command_id)
            self.engine.drop_press()
        elif action == "release":
            self.engine.drop_release()
        else:
            raise ValueError(f"Unknown drop action: {action}")
        self._publish_frame(self._published_frame())
        self.sequence += 1
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_color_hit(
        self,
        color: tuple[float, float, float] | None = None,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        rgb = Rgbw(r=color[0], g=color[1], b=color[2]) if color else None
        self.engine.trigger_color_hit(rgb)
        self._publish_frame(self._published_frame())
        self.sequence += 1
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_select_pad_slot(
        self,
        slot: int,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        pad = list(self.show.app.pad_presets)
        if slot < 0 or slot >= len(pad):
            raise ValueError(f"pad slot out of range: {slot}")
        preset_id = pad[slot]
        if preset_id not in self.engine.presets:
            raise ValueError(f"Pad preset {preset_id!r} is not loaded")
        return self.apply_select_preset(preset_id, client_command_id=client_command_id)

    def apply_sweep(
        self,
        action: str,
        *,
        mode: str = "horizontal",
        color: tuple[float, float, float] | None = None,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if action == "press":
            rgb = Rgbw(r=color[0], g=color[1], b=color[2]) if color else None
            resolved = "vertical" if mode == "vertical" else "horizontal"
            self.engine.sweep_press(mode=resolved, color=rgb)
        elif action == "release":
            self.engine.sweep_release()
        else:
            raise ValueError(f"Unknown sweep action: {action}")
        self._publish_frame(self._published_frame())
        self.sequence += 1
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_pad_presets(
        self,
        preset_ids: list[str],
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        from orng_led.config.io import save_model
        from orng_led.config.models import AppConfig

        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        app = self.show.app.model_copy(update={"pad_presets": list(preset_ids)})
        # Re-validate through AppConfig rules.
        app = AppConfig.model_validate(app.model_dump(mode="python"))
        missing = [pid for pid in app.pad_presets if pid not in self.engine.presets]
        if missing:
            raise ValueError(f"Unknown pad presets: {', '.join(missing)}")
        save_model(self.config_dir / "app.yaml", app)
        self.show = self.show.model_copy(update={"app": app})
        self.engine.show = self.show
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_live_fx_speeds(
        self,
        *,
        strobe_speed: float | None = None,
        sweep_speed: float | None = None,
        persist: bool = True,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        from orng_led.config.io import save_model

        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if strobe_speed is not None:
            self.engine.set_strobe_speed(strobe_speed)
        if sweep_speed is not None:
            self.engine.set_sweep_speed(sweep_speed)
        if persist:
            app = self.show.app.model_copy(
                update={
                    "strobe_speed": self.engine.overlays.strobe_speed,
                    "sweep_speed": self.engine.overlays.sweep_speed,
                }
            )
            save_model(self.config_dir / "app.yaml", app)
            self.show = self.show.model_copy(update={"app": app})
            self.engine.show = self.show
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_nudge_episode(
        self,
        delta: int,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        snap = self.engine.render_at(self.engine.clock.time(), dt_s=0.0)
        count = max(1, int(snap.episode_count))
        # Loop the show: last → next wraps to first, first → prev wraps to last.
        target = (int(snap.episode_index) + int(delta)) % count
        return self.apply_seek_episode(target, client_command_id=client_command_id)

    def apply_nudge_program_speed(
        self,
        delta: float,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        from orng_led.input.contract import (
            LIVE_FX_SPEED_STEP,
            PROGRAM_SPEED_MAX,
            PROGRAM_SPEED_MIN,
        )

        # Volume encoder while Strobe/Sweep held → live-FX speed only (not preset ×).
        if self.engine.overlays.strobe_held or self.engine.overlays.sweep_held:
            step = LIVE_FX_SPEED_STEP if delta > 0 else -LIVE_FX_SPEED_STEP
            return self.apply_nudge_live_fx_speed(step, client_command_id=client_command_id)

        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if self._tempo_holds_speed():
            state = self.build_state()
            self._remember(client_command_id, state)
            return state, False
        value = max(
            PROGRAM_SPEED_MIN,
            min(PROGRAM_SPEED_MAX, float(self.preview_speed) + float(delta)),
        )
        # Keep quarter-step UI values stable (×0.5 … ×5.0).
        value = round(value * 4.0) / 4.0
        return self.apply_preview_speed(value, client_command_id=client_command_id)

    def apply_nudge_live_fx_speed(
        self,
        delta: float,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        changed = self.engine.nudge_live_fx_speed(delta)
        if changed is None:
            state = self.build_state()
            self._remember(client_command_id, state)
            return state, False
        # Per-hold only — do not write app.yaml; next press restores the default.
        return self.apply_live_fx_speeds(
            strobe_speed=self.engine.overlays.strobe_speed,
            sweep_speed=self.engine.overlays.sweep_speed,
            persist=False,
            client_command_id=client_command_id,
        )

    def apply_zoom(
        self,
        delta: int,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        """Zoom encoder always steps episodes — never interrupts live FX holds.

        Live-FX speed while Strobe/Sweep is held is owned by the volume encoder
        (see ``apply_nudge_program_speed``), so Ctrl+wheel / ZoomIn/Out can change
        the underlying episode without zeroing or releasing the overlay.
        """
        return self.apply_nudge_episode(delta, client_command_id=client_command_id)

    def apply_sweep_hit(
        self,
        color: tuple[float, float, float] | None = None,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        return self.apply_sweep(
            "press",
            mode="horizontal",
            color=color,
            client_command_id=client_command_id,
        )

    def stage_layout(self) -> dict:
        """Static stage geometry for the simulator (no DMX values here)."""
        layout = self.show.layout
        kinds = {fx.id: fx.kind.value for fx in self.show.patch.fixtures}
        labels = {fx.id: fx.label for fx in self.show.patch.fixtures}
        groups = {fx.id: list(fx.groups) for fx in self.show.patch.fixtures}
        placements = []
        for placement in layout.placements:
            data = placement.model_dump(mode="json")
            data["label"] = labels.get(placement.fixture_id, placement.fixture_id)
            data["groups"] = groups.get(placement.fixture_id, [])
            data["kind"] = kinds.get(placement.fixture_id, data["kind"])
            placements.append(data)
        return {
            "description": layout.description,
            "placements": placements,
            "cable_chain": list(layout.cable_chain),
            "artnet_node": (
                layout.artnet_node.model_dump(mode="json") if layout.artnet_node else None
            ),
            "hardware_verified": False,
            "notes": "Provisional stage plan from the operator sketch. PENDING HARDWARE.",
        }

    def preview_clip(
        self,
        preset_id: str,
        *,
        seconds: float = 6.0,
        fps: int = 12,
        start_s: float = 0.0,
    ) -> dict:
        """Render a safe Mock preview clip with the real renderer, off-transport.

        The clip never touches the live engine, the output controller or any
        transport: it runs a throwaway engine over the same profiles/layout,
        so what the operator sees is exactly what the show renderer produces.
        """
        if preset_id not in self.preset_store.programs():
            raise KeyError(f"Unknown preset {preset_id!r}")
        fps = max(4, min(30, int(fps)))
        seconds = max(1.0, min(20.0, float(seconds)))
        programs = self.preset_store.programs()
        engine = Engine(show=self.show, presets=programs, active_preset_id=preset_id)
        engine.set_blackout(False)
        engine.overlays.master_brightness = 1.0
        dt = 1.0 / fps
        program = programs[preset_id]
        cycle = float(getattr(program, "total_duration_s", 0.0) or 0.0)
        # The window is the document, not a fixed 600 s. Seeking lands on that
        # time; the returned clip is still only ``seconds`` long.
        limit = cycle if cycle > 0.0 else 600.0
        start_s = max(0.0, min(limit, float(start_s)))
        engine.preset_elapsed_s = start_s
        engine._resync_phase_turns()
        from orng_led.engine.beam import BeamMotionState
        from orng_led.engine.intents import BeamIntent

        held = engine.active_preset.evaluate(start_s, engine.show)
        for fixture_id, intent in held.fixtures.items():
            if (
                isinstance(intent, BeamIntent)
                and intent.pan is not None
                and intent.tilt is not None
            ):
                engine.beam_motion[fixture_id] = BeamMotionState(
                    pan=float(intent.pan),
                    tilt=float(intent.tilt),
                    last_valid_pan=float(intent.pan),
                    last_valid_tilt=float(intent.tilt),
                )
        frames = []
        total = int(seconds * fps)
        for _ in range(total):
            snap = engine.tick(dt_s=dt, wall_dt_s=dt)
            view = decode_simulator_view(self.show, snap.frame)
            frames.append(view.model_dump(mode="json"))
        return {
            "preset_id": preset_id,
            "fps": fps,
            "seconds": seconds,
            "start_s": start_s,
            "transport": "none",
            "playback": "engine",
            "safe_mock_preview": True,
            "frames": frames,
        }

    def apply_blackout(
        self,
        enabled: bool | None = None,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        fingerprint = self._command_fingerprint("blackout", enabled=enabled)
        cached = self._idempotent(client_command_id, fingerprint)
        if cached is not None:
            return cached, True
        if enabled is None:
            self.engine.toggle_blackout()
        else:
            self.engine.set_blackout(enabled)
        snap = self.engine.render_at(self.engine.clock.time(), dt_s=0.0)
        self._last_engine_snap = snap
        self._publish_frame(self._published_frame(snap))
        self.sequence += 1
        state = self.build_state(reuse=True)
        self._remember(client_command_id, fingerprint)
        return state, False

    def apply_face(
        self,
        enabled: bool,
        brightness: float | None = None,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        self.engine.set_face(enabled, brightness)
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_master_brightness(
        self,
        value: float,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        self.engine.set_master_brightness(value)
        snap = self.engine.render_at(self.engine.clock.time(), dt_s=0.0)
        self._last_engine_snap = snap
        self._publish_frame(self._published_frame(snap))
        self.sequence += 1
        state = self.build_state(reuse=True)
        self._remember(client_command_id, state)
        return state, False

    def apply_head_desk(
        self,
        *,
        enabled: bool,
        light: bool,
        scene: bool,
        color: str,
        x: float,
        y: float,
    ) -> None:
        """Stick pose. Published immediately and held by the engine until the switch goes off."""
        self.engine.set_head_desk(
            enabled=enabled,
            light=light,
            scene=scene,
            color=color,
            x=x,
            y=y,
        )
        snap = self.engine.render_at(self.engine.clock.time(), dt_s=0.0)
        self._last_engine_snap = snap
        self._publish_frame(self._published_frame(snap))
        self.sequence += 1

    def apply_focus_loss(
        self,
        *,
        client_command_id: str | None = None,
        source_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        # Source-scoped: never clear linux / other-tab holds when a browser loses focus.
        if source_id and self.input_dispatcher is not None:
            result = self.input_dispatcher.force_release_source(source_id)
            state = result.state or self.build_state()
        elif self.input_dispatcher is not None:
            # Legacy payloads without source_id: clear everything (old single-tab contract).
            result = self.input_dispatcher.force_release_all()
            state = result.state or self.build_state()
        else:
            self.output.on_focus_loss()
            state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_visibility_hidden(
        self,
        *,
        client_command_id: str | None = None,
        source_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if source_id and self.input_dispatcher is not None:
            result = self.input_dispatcher.force_release_source(source_id)
            state = result.state or self.build_state()
        elif self.input_dispatcher is not None:
            result = self.input_dispatcher.force_release_all()
            state = result.state or self.build_state()
        else:
            self.output.on_visibility_hidden()
            state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def list_preset_summaries(self) -> list[dict]:
        return [item.model_dump(mode="json") for item in self.preset_store.summaries()]

    def get_preset_document(self, preset_id: str) -> dict:
        return self.preset_store.get(preset_id).model_dump(mode="json")

    def create_preset(self, data: dict) -> AppStateResponse:
        document = PresetDocument.model_validate(data)
        document = document.model_copy(update={"hardware_tuned": False, "builtin": False})
        self.preset_store.create(document)
        self.refresh_presets()
        return self.build_state()

    def create_default_custom(self, preset_id: str, label: str) -> AppStateResponse:
        document = self.preset_store.default_custom_document(preset_id, label)
        self.preset_store.create(document)
        self.refresh_presets()
        return self.build_state()

    def update_preset(self, preset_id: str, data: dict) -> AppStateResponse:
        self.preset_store.update(preset_id, data)
        self.refresh_presets()
        if self.engine.active_preset_id == preset_id and not self.engine.editor_preview_active:
            # Refresh live definition and restart current (or first) episode.
            try:
                current = int(
                    self.engine.render_at(self.engine.clock.time(), dt_s=0.0).episode_index
                )
                self.engine.seek_episode(current)
            except (ValueError, KeyError):
                self.engine.select_preset(preset_id, reset_clock=True)
            self._publish_frame(self._published_frame())
            self.sequence += 1
        return self.build_state()

    def rename_preset(self, preset_id: str, label: str) -> AppStateResponse:
        self.preset_store.rename(preset_id, label)
        self.refresh_presets()
        return self.build_state()

    def duplicate_preset(
        self, preset_id: str, new_id: str, new_label: str | None = None
    ) -> AppStateResponse:
        self.preset_store.duplicate(preset_id, new_id, new_label)
        self.refresh_presets()
        return self.build_state()

    def delete_preset(self, preset_id: str) -> AppStateResponse:
        from orng_led.engine.presets import NONE_PRESET_ID

        was_active = self.engine.active_preset_id == preset_id
        previewing = (
            self.engine.editor_preview_active
            and self._editor_preview_meta.get("preset_id") == preset_id
        )
        if previewing:
            self._clear_editor_preview(restore=True)
        self.preset_store.delete(preset_id)
        self.refresh_presets()
        if was_active:
            # Stop playback → NONE; do not touch Art-Net / Arm / Blackout.
            self.engine.select_preset(NONE_PRESET_ID, reset_clock=True)
            self._publish_frame(self._published_frame())
            self.sequence += 1
        return self.build_state()

    def _clear_editor_preview(self, *, restore: bool) -> None:
        from orng_led.engine.presets import NONE_PRESET_ID

        restored = str(self._editor_preview_meta.get("restored_preset_id") or NONE_PRESET_ID)
        self.engine.stop_editor_preview()
        self._editor_preview_meta = {}
        if restore:
            if restored not in self.engine.presets:
                restored = NONE_PRESET_ID
            # Keep pad clock where it was paused; do not touch safety gates.
            self.engine.select_preset(restored, reset_clock=False)
        self._publish_frame(self._published_frame())
        self.sequence += 1

    def start_editor_episode_preview(
        self,
        *,
        preset_id: str,
        preset_label: str,
        episode_index: int,
        episode: dict,
    ) -> AppStateResponse:
        """Loop one draft episode through the real renderer/mapping (no auto Arm)."""
        from pydantic import ValidationError

        from orng_led.config.schema import SCHEMA_VERSION
        from orng_led.presets.models import EpisodeCard, PresetDocument
        from orng_led.presets.program import YamlPresetProgram

        try:
            card = EpisodeCard.model_validate(episode)
        except ValidationError as exc:
            # Surface the first concrete field error for the operator UI.
            err = exc.errors()[0]
            loc = ".".join(str(part) for part in err.get("loc", ()))
            raise ValueError(f"{loc or 'episode'}: {err.get('msg', 'invalid')}") from exc

        # Single-episode document so the program loops only this look.
        document = PresetDocument(
            schema_version=SCHEMA_VERSION,
            id="EditorPreview",
            label=preset_label[:80] or "Чернетка",
            hardware_tuned=False,
            builtin=False,
            episodes=[card],
        )
        program = YamlPresetProgram(document=document)

        if self.raw_tester.active:
            # Editor preview must own the engine base path, not Raw channels.
            self.exit_raw_tester()
        if self.beam_calibration.active:
            self.end_beam_calibration_test()

        if not self.engine.editor_preview_active:
            self._editor_preview_meta = {
                "restored_preset_id": self.engine.active_preset_id,
            }
        self._editor_preview_meta.update(
            {
                "preset_id": preset_id,
                "preset_label": preset_label,
                "episode_index": int(episode_index),
                "episode_id": card.id,
                "episode_title": (
                    f"Епізод {int(episode_index) + 1} · {card.effect} · {card.palette}"
                ),
            }
        )
        self.engine.start_editor_preview(program)
        self._publish_frame(self._published_frame())
        self.sequence += 1
        return self.build_state()

    def stop_editor_episode_preview(self) -> AppStateResponse:
        if self.engine.editor_preview_active:
            self._clear_editor_preview(restore=True)
        return self.build_state()

    def preview_preset(self, preset_id: str, *, speed: float = 10.0) -> AppStateResponse:
        """Select preset and accelerate preview on Mock only (never arms Art-Net)."""
        if self.engine.editor_preview_active:
            self._clear_editor_preview(restore=False)
        self.output.use_mock()
        if self.raw_tester.active:
            self.exit_raw_tester()
        self.engine.select_preset(preset_id, reset_clock=True)
        # Preview is an explicit operator action: clear startup blackout so Mock is visible.
        self.engine.set_blackout(False)
        from orng_led.input.contract import PROGRAM_SPEED_MIN

        self.preview_speed = max(PROGRAM_SPEED_MIN, min(120.0, float(speed)))
        self._publish_frame(self._published_frame())
        self.sequence += 1
        return self.build_state()

    def list_scenes(self) -> list[dict[str, object]]:
        store = SceneStore(self.config_root)
        return [
            {
                "scene_id": item.scene_id,
                "name": item.name,
                "path": item.path,
                "error": item.error,
                "active": item.scene_id == self.scene_id and item.error is None,
            }
            for item in store.list_scenes()
        ]

    def active_scene_info(self) -> dict[str, object]:
        return {
            "scene_id": self.scene_id,
            "name": self.scene_name,
            "path": str(self.config_dir),
            "error": self.scene_error,
        }

    def activate_scene(
        self,
        scene_id: str,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        """Safely switch scenes: blackout + disarm, validate, then replace.

        On failure the previous scene stays loaded and ``scene_error`` is set.
        Never sends an old-scene frame through a new patch.
        Successful idempotent replays return ``(state, True)`` without re-switching.
        """
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True

        store = SceneStore(self.config_root)
        previous_error = self.scene_error
        try:
            bundle = store.load_scene(scene_id)
        except (ConfigError, OSError, ValueError) as exc:
            self.scene_error = str(exc)
            # Do not cache failures as successful commands.
            raise ConfigError(str(exc)) from exc

        # 1–5: end editor / raw / beam cal, release holds, blackout, dark frames, disarm.
        if self.engine.editor_preview_active:
            self._clear_editor_preview(restore=False)
        if self.raw_tester.active:
            self.exit_raw_tester()
        if self.beam_calibration.active:
            self.end_beam_calibration_test()
        if self.input_dispatcher is not None:
            self.input_dispatcher.force_release_all()
        self.engine.set_blackout(True)
        try:
            self.output._emit_zero_frames(count=3, raise_on_error=False)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
        try:
            self.output.disarm()
        except Exception:  # noqa: BLE001
            self.output.armed = False
        try:
            self.output.deactivate_to_mock(zero_count=1)
        except Exception:  # noqa: BLE001
            self.output.use_mock()

        # 6: already validated via load_scene above.
        scene_dir = Path(bundle.scene_dir)
        try:
            preset_store = PresetStore.load(scene_dir / "presets")
            new_engine = Engine(show=bundle.show, presets=preset_store.programs())
            new_engine.set_blackout(True)
            new_engine.overlays.master_brightness = bundle.show.app.master_brightness
            new_engine.set_strobe_speed(bundle.show.app.strobe_speed)
            new_engine.set_sweep_speed(bundle.show.app.sweep_speed)
        except (ConfigError, OSError, ValueError, KeyError) as exc:
            self.scene_error = str(exc)
            # Keep old scene; re-assert blackout safety on the old patch.
            self.engine.set_blackout(True)
            self._publish_frame(self._published_frame())
            raise ConfigError(str(exc)) from exc

        # 7–8: only now replace show / engine / presets / mapping / config_dir.
        # Build a fresh OutputController bound to the new engine so old frames
        # cannot be published through the new patch.
        new_output = OutputController(engine=new_engine)
        self.show = bundle.show
        self.engine = new_engine
        self.output = new_output
        self.preset_store = preset_store
        self.config_dir = scene_dir
        self.scene_id = bundle.manifest.scene_id
        self.scene_name = bundle.manifest.name
        self.bpm.set_scene(self.scene_id)
        self._reset_musical_identity()
        self._manual_speed = None
        self.scene_controls = bundle.controls
        self.scene_error = None
        self.preview_speed = 1.0
        self._editor_preview_meta = {}
        self.refresh_presets()
        if self.input_dispatcher is not None:
            self.input_dispatcher.holds.clear()
            self.input_dispatcher.debouncer.clear()
        linux_kb = self._linux_keyboard
        if linux_kb is not None and hasattr(linux_kb, "notify_controls_changed"):
            linux_kb.notify_controls_changed()
        # Persist after successful swap.
        try:
            store.save_active_scene_id(bundle.manifest.scene_id)
        except ConfigError as exc:
            # Scene is live; surface persist failure without rolling back.
            self.scene_error = f"scene active but runtime.yaml persist failed: {exc}"
        self._publish_frame(self._published_frame())
        # Venue: reopen Art-Net + arm under blackout after safe scene swap.
        self._ensure_venue_show_ready()
        self.sequence += 1
        _ = previous_error
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def linux_keyboard_status(self) -> dict[str, object]:
        adapter = self._linux_keyboard
        if adapter is None:
            import sys

            from orng_led.input.linux_keyboard import (
                LinuxKeyboardStatus,
                evdev_available,
                linux_keyboard_enabled,
            )

            status = LinuxKeyboardStatus(
                enabled=linux_keyboard_enabled(),
                supported=sys.platform.startswith("linux") and evdev_available(),
            )
            if status.enabled and not status.supported:
                status.last_error = "linux keyboard adapter not started"
            return status.as_dict()
        return adapter.status.as_dict()  # type: ignore[attr-defined]

    def on_ws_disconnect(self, source_id: str | None = None) -> None:
        """Release only this connection's holds — never clear all sources."""
        if self.beam_calibration.active:
            self.end_beam_calibration_test()
        if source_id and self.input_dispatcher is not None:
            self.input_dispatcher.force_release_source(source_id)
        # Do not call output.on_disconnect() — that clears every hold.

    def subscribe(self, sender: WsSender) -> None:
        self._subscribers.add(sender)

    def unsubscribe(self, sender: WsSender) -> None:
        self._subscribers.discard(sender)
        self._sender_latest.pop(sender, None)
        task = self._sender_tasks.pop(sender, None)
        if task is not None and not task.done():
            task.cancel()

    def attach_socket(self, client: object) -> None:
        self._sockets.add(client)

    def detach_socket(self, client: object) -> None:
        self._sockets.discard(client)

    def frame_timing(self) -> dict[str, object]:
        """Recent frame costs in milliseconds. Not a recording of the show."""

        def pack(samples: deque[float]) -> dict[str, float | int | None]:
            values = list(samples)
            if not values:
                return {"n": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None}

            ordered = sorted(values)

            def at(q: float) -> float:
                index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
                return round(ordered[index] * 1000, 2)

            return {
                "n": len(values),
                "p50_ms": at(0.50),
                "p95_ms": at(0.95),
                "p99_ms": at(0.99),
                "max_ms": round(max(values) * 1000, 2),
            }

        transport = getattr(self.output, "transport", None)
        calc_intervals = [lag + FRAME_DT for lag in self._lag_samples]
        return {
            "tick": pack(self._tick_samples),
            "engine": pack(self._engine_samples),
            "send": pack(self._send_samples),
            "poll": pack(self._poll_samples),
            "state": pack(self._state_samples),
            "loop_lag": pack(self._lag_samples),
            "scheduler_wait": pack(self._scheduler_samples),
            "sleep": pack(self._sleep_samples),
            "calc_interval": pack(calc_intervals),
            "calc_gaps_over_100ms": [
                round(interval * 1000, 1) for interval in calc_intervals if interval > 0.1
            ],
            "send_blocked": int(getattr(transport, "send_blocked", 0) or 0),
            "capture": getattr(self.bpm, "capture", None),
        }

    def request_broadcast(self) -> None:
        """Ask the state thread for one snapshot. The frame loop does not build it."""
        if not self._subscribers and not self._sockets:
            return
        try:
            self._aio_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        self._ensure_broadcast_thread()
        self._broadcast_event.set()

    def _ensure_broadcast_thread(self) -> None:
        thread = self._broadcast_thread
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(
            target=self._broadcast_worker, name="orng-state", daemon=True
        )
        self._broadcast_thread = thread
        thread.start()

    def _broadcast_worker(self) -> None:
        """Build and serialize desk state off the Art-Net thread."""
        while not self._shutting_down:
            self._broadcast_event.wait(0.5)
            if self._shutting_down:
                return
            if not self._broadcast_event.is_set():
                continue
            self._broadcast_event.clear()
            if not self._subscribers and not self._sockets:
                continue
            started = time.perf_counter()
            try:
                payload = {
                    "type": "state",
                    "state": self.build_state(reuse=True).model_dump(mode="json"),
                }
            except Exception:  # noqa: BLE001 — a desk snapshot must not stop output
                logger.debug("state snapshot failed", exc_info=True)
                continue
            self._state_samples.append(time.perf_counter() - started)
            if self._broadcast_event.is_set():
                continue
            loop = self._aio_loop
            if loop is None or loop.is_closed():
                continue
            try:
                loop.call_soon_threadsafe(self._fanout_state, payload)
            except RuntimeError:
                continue

    def _fanout_state(self, payload: dict) -> None:
        dead: list[WsSender] = []
        for sender in list(self._subscribers):
            offer = getattr(sender, "offer_state", None)
            try:
                if offer is not None:
                    offer(payload)
                else:
                    # One in-flight send per bare sender. Newer snapshots replace
                    # the pending one; ACK and release stay on the command queue.
                    self._sender_latest[sender] = payload
                    task = self._sender_tasks.get(sender)
                    if task is None or task.done():
                        self._arm_bare_sender(sender)
            except Exception:  # noqa: BLE001
                dead.append(sender)
        for sender in dead:
            self.unsubscribe(sender)
        for client in list(self._sockets):
            offer = getattr(client, "offer_state", None)
            if offer is None:
                self._sockets.discard(client)
                continue
            try:
                offer(payload)
            except Exception:  # noqa: BLE001
                self._sockets.discard(client)

    def _arm_bare_sender(self, sender: WsSender) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._sender_tasks[sender] = loop.create_task(self._drain_bare_sender(sender))

    async def _drain_bare_sender(self, sender: WsSender) -> None:
        try:
            while sender in self._subscribers:
                payload = self._sender_latest.pop(sender, None)
                if payload is None:
                    return
                await sender(payload)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            self._subscribers.discard(sender)
            self._sender_latest.pop(sender, None)
        finally:
            try:
                current_task = asyncio.current_task()
            except RuntimeError:
                current_task = None
            if current_task is not None and self._sender_tasks.get(sender) is current_task:
                self._sender_tasks.pop(sender, None)
            if (
                current_task is not None
                and sender in self._subscribers
                and sender in self._sender_latest
            ):
                self._arm_bare_sender(sender)

    async def broadcast_state(self) -> None:
        self.request_broadcast()

    async def shutdown(self) -> list[list[int]]:
        if self._shutting_down:
            return []
        self._shutting_down = True
        self._running = False
        self._broadcast_event.set()
        worker = self._travel_worker
        self._travel_worker = None
        if worker is not None:
            close = getattr(worker, "close", None)
            if callable(close):
                close()
        try:
            from orng_led.input.windows_media_keys import stop_volume_hook_for_runtime

            stop_volume_hook_for_runtime(self)
        except Exception:  # noqa: BLE001
            pass
        linux = self._linux_keyboard
        self._linux_keyboard = None
        if linux is not None:
            try:
                await linux.stop()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        cloud = self._cloud_agent
        self._cloud_agent = None
        if cloud is not None:
            try:
                await cloud.stop()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        await self._stop_venue_ready_supervisor()
        bpm = getattr(self, "_bpm_service", None)
        if bpm is not None:
            bpm.stop()
        if self.input_dispatcher is not None:
            self.input_dispatcher.force_release_all()
        if self.engine.editor_preview_active:
            self.engine.stop_editor_preview()
            self._editor_preview_meta = {}
        if self.beam_calibration.active:
            self.end_beam_calibration_test()
        if self.raw_tester.active:
            self.exit_raw_tester()
        task = self._loop_task
        self._loop_task = None
        if task is not None:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 — failed cycle must not skip cleanup
                pass
        emitted = self.output.shutdown()
        for sender in list(self._subscribers):
            self.unsubscribe(sender)
        self._subscribers.clear()
        return emitted
