import { onMounted, onUnmounted } from "vue";
import { postInputKeyboard } from "../api/input";

type StateHandler = (state: import("../vite-env").AppState) => void;

const PREVENT_DEFAULT_CODES = new Set([
  "Digit0",
  "Digit1",
  "Digit2",
  "Digit3",
  "Digit4",
  "Digit5",
  "Digit6",
  "Digit7",
  "Digit8",
  "Digit9",
  "Space",
  "Backspace",
  "Enter",
  "NumpadEnter",
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

/**
 * HOME keyboard / encoder adapter → /api/input/keyboard.
 * 0 = NONE; 1–9 = pad slots; Space blackout; Backspace strobe; Enter sweep;
 * Zoom (± / ZoomIn/Out) = episode or live-FX speed; Volume = program ×1–×5.
 */
export function useKeyboardPad(options: {
  enabled: () => boolean;
  onState: StateHandler;
  onIgnored?: (reason: string) => void;
}) {
  async function handleKey(event: KeyboardEvent, type: "keydown" | "keyup") {
    if (!options.enabled()) return;
    const target = event.target as HTMLElement | null;
    if (target) {
      const tag = target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable) {
        return;
      }
      // Avoid double-firing with focused MomentaryButton / native button semantics.
      if (
        (tag === "BUTTON" || target.closest("button,[role='button']")) &&
        (event.code === "Space" ||
          event.code === "Enter" ||
          event.code === "NumpadEnter" ||
          event.code === "Backspace")
      ) {
        return;
      }
    }

    try {
      const ack = await postInputKeyboard({
        code: event.code,
        type,
        repeat: event.repeat,
      });
      if (ack.accepted) {
        options.onState(ack.state);
        if (PREVENT_DEFAULT_CODES.has(event.code)) {
          event.preventDefault();
        }
      } else if (ack.reason && options.onIgnored) {
        options.onIgnored(ack.reason);
      }
    } catch {
      // offline — ignore keyboard while backend unreachable
    }
  }

  function onKeyDown(event: KeyboardEvent) {
    void handleKey(event, "keydown");
  }

  function onKeyUp(event: KeyboardEvent) {
    void handleKey(event, "keyup");
  }

  onMounted(() => {
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
  });

  onUnmounted(() => {
    window.removeEventListener("keydown", onKeyDown);
    window.removeEventListener("keyup", onKeyUp);
  });
}
