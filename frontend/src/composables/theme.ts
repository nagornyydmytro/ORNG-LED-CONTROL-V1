/** Session-scoped UI theme (dark default, light = liquid-glass). */

export type UiTheme = "dark" | "light";

export const THEME_STORAGE_KEY = "orng-ui-theme";

export function readStoredTheme(): UiTheme {
  try {
    const value = sessionStorage.getItem(THEME_STORAGE_KEY);
    if (value === "light" || value === "dark") return value;
  } catch {
    // private mode / blocked storage
  }
  return "dark";
}

export function applyTheme(theme: UiTheme): void {
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
}

export function persistTheme(theme: UiTheme): void {
  try {
    sessionStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // ignore
  }
}

/** Call once before Vue mount to avoid a flash of the wrong theme. */
export function bootTheme(): UiTheme {
  const theme = readStoredTheme();
  applyTheme(theme);
  return theme;
}
