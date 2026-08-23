import { onMounted, onUnmounted } from "vue";
import { postInputKeyboard } from "../api/input";

type StateHandler = (state: import("../vite-env").AppState) => void;

/** Keys that must never trigger browser defaults (scroll, shortcuts, etc.). */
const PREVENT_DEFAULT_CODES = new Set([
  "KeyQ",
  "KeyW",
  "KeyE",
  "KeyR",
  "KeyT",
  "KeyY",
  "KeyU",
  "KeyI",
  "KeyO",
  "KeyP",
  "Space",
  "KeyZ",
  "KeyX",
  "KeyC",
  "Delete",
  "Backspace",
  "Enter",
  "NumpadEnter",
  "Escape",
  "KeyF",
  "Minus",
  "Equal",
  "NumpadSubtract",
  "NumpadAdd",
  "ZoomIn",
  "ZoomOut",
  "AudioVolumeUp",
  "AudioVolumeDown",
  "VolumeUp",
  "VolumeDown",
]);

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

/**
 * HOME keyboard / encoder adapter → /api/input/keyboard.
 * Q–O = pad slots 1–9; P = NONE;
 * Space = single-press blackout (no page scroll);
 * Z hold = strobe, X hold = sweep, Delete hold = vertical sweep (C alias);
 * Escape = face PARs toggle (F alias);
 * Zoom (± / ZoomIn/Out / Ctrl+wheel) = episode ±1 (keeps Strobe/Sweep held);
 * Volume = program ×0.5–×5, or live-FX speed while Strobe/Sweep held.
 */
/** Hold keys: press/release must stay ordered (parallel fetch can invert edges). */
const HOLD_EDGE_CODES = new Set([
  "KeyZ",
  "KeyX",
  "KeyC",
  "Delete",
  "Backspace",
  "Enter",
  "NumpadEnter",
]);

export function useKeyboardPad(options: {
  enabled: () => boolean;
  onState: StateHandler;
  onIgnored?: (reason: string) => void;
}) {
  let lastWheelZoomAt = 0;
  const holdChains = new Map<string, Promise<void>>();

  async function dispatchCode(
    code: string,
    type: "keydown" | "keyup",
    repeat = false,
  ) {
    try {
      const ack = await postInputKeyboard({ code, type, repeat });
      if (ack.accepted) {
        options.onState(ack.state);
      } else if (ack.reason && options.onIgnored) {
        options.onIgnored(ack.reason);
      }
    } catch {
      // offline — ignore keyboard while backend unreachable
    }
  }

  function dispatchHoldEdge(code: string, type: "keydown" | "keyup", repeat: boolean) {
    const prev = holdChains.get(code) ?? Promise.resolve();
    const next = prev
      .then(() => dispatchCode(code, type, repeat))
      .then(() => undefined)
      .catch(() => undefined);
    holdChains.set(code, next);
    return next;
  }

  async function handleKey(event: KeyboardEvent, type: "keydown" | "keyup") {
    if (!options.enabled()) return;
    const target = event.target as HTMLElement | null;
    if (isTypingTarget(target)) return;

    // Avoid double-firing with focused MomentaryButton / native button semantics.
    // Backspace is not a button activator — still allow global strobe hold.
    if (
      target &&
      (target.tagName === "BUTTON" || target.closest("button,[role='button']")) &&
      (event.code === "Space" ||
        event.code === "Enter" ||
        event.code === "NumpadEnter")
    ) {
      return;
    }

    const code =
      event.code ||
      (event.key === "AudioVolumeUp" || event.key === "VolumeUp"
        ? "AudioVolumeUp"
        : event.key === "AudioVolumeDown" || event.key === "VolumeDown"
          ? "AudioVolumeDown"
          : "");
    if (!code) return;

    // Block browser defaults synchronously — after await is too late (Space scrolls).
    if (type === "keydown" && PREVENT_DEFAULT_CODES.has(code)) {
      event.preventDefault();
    }
    // Hold keyup also needs preventDefault so browser back-nav / form submit stays off.
    if (type === "keyup" && HOLD_EDGE_CODES.has(code)) {
      event.preventDefault();
    }

    if (HOLD_EDGE_CODES.has(code)) {
      await dispatchHoldEdge(code, type, event.repeat);
      return;
    }
    await dispatchCode(code, type, event.repeat);
  }

  function onKeyDown(event: KeyboardEvent) {
    // Space is a one-shot toggle — ignore auto-repeat while held.
    if (event.code === "Space" && event.repeat) {
      if (!isTypingTarget(event.target)) event.preventDefault();
      return;
    }
    void handleKey(event, "keydown");
  }

  function onKeyUp(event: KeyboardEvent) {
    // Space has no release action; only hold keys (strobe/sweep) need keyup.
    if (event.code === "Space") return;
    void handleKey(event, "keyup");
  }

  /** Hardware zoom knobs often emit Ctrl+wheel; same as ZoomIn/ZoomOut → episodes. */
  function onWheel(event: WheelEvent) {
    if (!options.enabled()) return;
    if (!event.ctrlKey && !event.metaKey) return;
    if (isTypingTarget(event.target)) return;

    // Always block browser page zoom for this gesture on the console.
    event.preventDefault();

    const now = performance.now();
    if (now - lastWheelZoomAt < 80) return;
    lastWheelZoomAt = now;

    // deltaY < 0 = wheel up = zoom in = next episode (same as ZoomIn / Equal).
    const code = event.deltaY < 0 ? "ZoomIn" : "ZoomOut";
    void dispatchCode(code, "keydown", false);
  }

  onMounted(() => {
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("wheel", onWheel, { passive: false });
  });

  onUnmounted(() => {
    window.removeEventListener("keydown", onKeyDown);
    window.removeEventListener("keyup", onKeyUp);
    window.removeEventListener("wheel", onWheel);
  });
}
