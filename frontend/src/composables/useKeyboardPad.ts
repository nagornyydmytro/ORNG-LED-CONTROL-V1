import { onMounted, onUnmounted } from "vue";
import { postInputKeyboard } from "../api/input";

type StateHandler = (state: import("../vite-env").AppState) => void;

/**
 * HOME keyboard/mock adapter: browser keys → /api/input/keyboard contract.
 * 1–9,0 → P01–P10; F face; H white hit; S strobe hold; B blackout; -/+ brightness.
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
    }

    try {
      const ack = await postInputKeyboard({
        code: event.code,
        type,
        repeat: event.repeat,
      });
      if (ack.accepted) {
        options.onState(ack.state);
        if (type === "keydown" && !event.repeat) {
          // Prevent browser shortcuts for mapped pad keys when accepted.
          if (
            event.code.startsWith("Digit") ||
            event.code === "KeyS" ||
            event.code === "KeyB" ||
            event.code === "KeyF" ||
            event.code === "KeyH" ||
            event.code === "Minus" ||
            event.code === "Equal"
          ) {
            event.preventDefault();
          }
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
