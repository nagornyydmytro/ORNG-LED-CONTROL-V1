import { beforeEach, describe, expect, it } from "vitest";
import {
  THEME_STORAGE_KEY,
  applyTheme,
  bootTheme,
  persistTheme,
  readStoredTheme,
} from "./theme";

describe("theme session persistence", () => {
  beforeEach(() => {
    sessionStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.style.colorScheme = "";
  });

  it("defaults to dark when nothing is stored", () => {
    expect(readStoredTheme()).toBe("dark");
    expect(bootTheme()).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("persists light theme across boot within the same session", () => {
    persistTheme("light");
    expect(sessionStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(bootTheme()).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("applyTheme updates the document root for CSS tokens", () => {
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
