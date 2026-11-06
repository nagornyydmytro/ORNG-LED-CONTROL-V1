export type InputProfileId = "qwerty" | "ch57x";

const STORAGE_KEY = "orng-input-profile";

export function loadInputProfile(): InputProfileId {
  try {
    return sessionStorage.getItem(STORAGE_KEY) === "ch57x" ? "ch57x" : "qwerty";
  } catch {
    return "qwerty";
  }
}

export function saveInputProfile(id: InputProfileId): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, id);
  } catch {
    // private mode
  }
}

/** Hold codes for the selected profile. QWERTY is Z/X/C. CH57x observed map is Q/T/O. */
export function holdCodesFor(id: InputProfileId): Set<string> {
  if (id === "ch57x") return new Set(["KeyQ", "KeyT", "KeyO"]);
  return new Set(["KeyZ", "KeyX", "KeyC"]);
}
