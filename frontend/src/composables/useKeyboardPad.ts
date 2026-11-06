import { onMounted, onUnmounted } from "vue";
import { postInputKeyboard } from "../api/input";
import { holdCodesFor, loadInputProfile } from "../lib/inputProfile";
import { getWebSessionSourceId } from "../lib/sessionSourceId";

type StateHandler = (state: import("../vite-env").AppState) => void;

/**
 * Canonical HOME keys (all programs):
 * QWERTYUIO, P=NONE (laptop/UI only — no key on CH57x macropad),
 * ZXC, Escape, Space, Volume±, Zoom±.
 */
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
  "Escape",
  "Minus",
  "Equal",
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

/** Ctrl/Cmd +/-/0 is the browser zoom shortcut, not a pad episode key. */
function isBrowserZoom(event: KeyboardEvent): boolean {
  if (!event.ctrlKey && !event.metaKey) return false;
  return (
    event.code === "Minus" ||
    event.code === "Equal" ||
    event.code === "Digit0" ||
    event.code === "NumpadSubtract" ||
    event.code === "NumpadAdd" ||
    event.code === "Numpad0"
  );
}

/**
 * HOME keyboard / encoder adapter → /api/input/keyboard.
 * Q–O = pad slots 0–8; P = NONE (no physical macropad key);
 * Space = blackout; Escape = face;
 * Z/X/C hold = strobe / horizontal sweep / vertical sweep;
 * Zoom = episode ±1; Volume = program speed (or live-FX speed while FX held).
 */
export function useKeyboardPad(options: {
  enabled: () => boolean;
  onState: StateHandler;
  onIgnored?: (reason: string) => void;
  /** When set, raw codes go through this transport instead of localhost. */
  dispatch?: (event: {
    code: string;
    type: "keydown" | "keyup";
    repeat: boolean;
    source_id: string;
    profile_id: "qwerty" | "ch57x";
  }) => Promise<void>;
}) {
  let lastWheelZoomAt = 0;
  let activeProfile = loadInputProfile();
  let swallowEscapeUp = false;
  const heldCodes = new Set<string>();

  async function dispatchCode(
    code: string,
    type: "keydown" | "keyup",
    repeat = false,
  ) {
    try {
      const source_id = getWebSessionSourceId();
      const profile_id = activeProfile;
      if (options.dispatch) {
        await options.dispatch({ code, type, repeat, source_id, profile_id });
        return;
      }
      const ack = await postInputKeyboard({
        code,
        type,
        repeat,
        source_id,
        profile_id,
      });
      if (ack.accepted) {
        options.onState(ack.state);
      } else if (ack.reason && options.onIgnored) {
        options.onIgnored(ack.reason);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Клавіатуру не застосовано";
      options.onIgnored?.(message);
    }
  }

  function dispatchHoldEdge(code: string, type: "keydown" | "keyup", repeat: boolean) {
    if (type === "keydown" && repeat) return;
    if (type === "keydown") heldCodes.add(code);
    if (type === "keyup") heldCodes.delete(code);
    void dispatchCode(code, type, repeat);
  }

  function onProfileChange(event: Event) {
    const next = (event as CustomEvent<"qwerty" | "ch57x">).detail;
    if (!next || next === activeProfile) return;
    const previous = activeProfile;
    const codes = [...heldCodes];
    heldCodes.clear();
    activeProfile = next;
    for (const code of codes) {
      activeProfile = previous;
      void dispatchCode(code, "keyup", false);
    }
    activeProfile = next;
  }

  async function handleKey(event: KeyboardEvent, type: "keydown" | "keyup") {
    const code =
      event.code ||
      (event.key === "AudioVolumeUp" || event.key === "VolumeUp"
        ? "AudioVolumeUp"
        : event.key === "AudioVolumeDown" || event.key === "VolumeDown"
          ? "AudioVolumeDown"
          : "");
    if (!code) return;
    if (isBrowserZoom(event)) return;
    if (!options.enabled()) {
      releaseHeld();
      return;
    }
    if (code === "Escape" && document.querySelector("[role='dialog']")) {
      if (type === "keydown") swallowEscapeUp = true;
      return;
    }
    if (code === "Escape" && type === "keyup" && swallowEscapeUp) {
      swallowEscapeUp = false;
      return;
    }
    const target = event.target as HTMLElement | null;
    if (isTypingTarget(target)) {
      if (type === "keyup" && heldCodes.has(code)) {
        heldCodes.delete(code);
        void dispatchCode(code, "keyup", false);
      }
      return;
    }

    // Avoid double-firing with focused MomentaryButton / native button semantics.
    if (
      target instanceof Element &&
      (target.tagName === "BUTTON" || target.closest("button,[role='button']")) &&
      event.code === "Space"
    ) {
      return;
    }

    // Block browser defaults synchronously — after await is too late (Space scrolls).
    if (type === "keydown" && PREVENT_DEFAULT_CODES.has(code)) {
      event.preventDefault();
    }
    // Hold keyup also needs preventDefault.
    const holds = holdCodesFor(activeProfile);
    if (type === "keyup" && holds.has(code)) {
      event.preventDefault();
    }

    if (holds.has(code)) {
      await dispatchHoldEdge(code, type, event.repeat);
      return;
    }
    await dispatchCode(code, type, event.repeat);
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.repeat && (event.code === "Space" || holdCodesFor(activeProfile).has(event.code))) {
      if (!isTypingTarget(event.target)) event.preventDefault();
      return;
    }
    void handleKey(event, "keydown");
  }

  function onKeyUp(event: KeyboardEvent) {
    // Space has no release action; only hold keys need keyup.
    if (event.code === "Space") return;
    void handleKey(event, "keyup");
  }

  /** Hardware zoom knobs often emit Ctrl+wheel; same as ZoomIn/ZoomOut → episodes. */
  function onWheel(event: WheelEvent) {
    if (!options.enabled()) {
      releaseHeld();
      return;
    }
    if (!event.ctrlKey && !event.metaKey) return;
    const pad =
      event.target instanceof Element ? event.target.closest("[data-light-pad]") : null;
    if (!pad || isTypingTarget(event.target)) return;

    event.preventDefault();

    const now = performance.now();
    if (now - lastWheelZoomAt < 80) return;
    lastWheelZoomAt = now;

    // deltaY < 0 = wheel up = zoom in = next episode (same as ZoomIn / Equal).
    const code = event.deltaY < 0 ? "ZoomIn" : "ZoomOut";
    void dispatchCode(code, "keydown", false);
  }

  function releaseHeld() {
    for (const code of [...heldCodes]) {
      void dispatchCode(code, "keyup", false);
    }
    heldCodes.clear();
  }

  function onBlur() {
    releaseHeld();
  }

  function onReleaseHolds() {
    releaseHeld();
  }

  onMounted(() => {
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    window.addEventListener("wheel", onWheel, { passive: false });
    window.addEventListener("orng-input-profile", onProfileChange);
    window.addEventListener("orng-release-holds", onReleaseHolds);
    window.addEventListener("blur", onBlur);
  });

  onUnmounted(() => {
    window.removeEventListener("keydown", onKeyDown, true);
    window.removeEventListener("keyup", onKeyUp, true);
    window.removeEventListener("wheel", onWheel);
    window.removeEventListener("orng-input-profile", onProfileChange);
    window.removeEventListener("orng-release-holds", onReleaseHolds);
    window.removeEventListener("blur", onBlur);
    for (const code of [...heldCodes]) {
      void dispatchCode(code, "keyup", false);
    }
    heldCodes.clear();
  });
}
