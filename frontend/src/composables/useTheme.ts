import { computed, ref } from "vue";
import {
  applyTheme,
  bootTheme,
  persistTheme,
  readStoredTheme,
  type UiTheme,
} from "./theme";

const theme = ref<UiTheme>(bootTheme());

export function useTheme() {
  const isLight = computed(() => theme.value === "light");

  function setTheme(next: UiTheme) {
    theme.value = next;
    applyTheme(next);
    persistTheme(next);
  }

  function toggleTheme() {
    setTheme(theme.value === "light" ? "dark" : "light");
  }

  /** Keep in sync if another tab of the same session updates storage. */
  function syncFromStorage() {
    const stored = readStoredTheme();
    if (stored !== theme.value) {
      theme.value = stored;
      applyTheme(stored);
    }
  }

  return {
    theme,
    isLight,
    setTheme,
    toggleTheme,
    syncFromStorage,
  };
}
