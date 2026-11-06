import { ref } from "vue";

/** One login for the operator shell, the service console, and the releases desk. */
export const adminAuthed = ref(false);
export const adminAuthChecked = ref(false);
export const adminUsername = ref<string | null>(null);
/** Bumps only when a live session ends, so in-flight commands can be dropped. */
export const adminSessionEpoch = ref(0);

export function markAdminAuthed(username: string | null): void {
  adminAuthed.value = true;
  adminUsername.value = username;
  adminAuthChecked.value = true;
}

export function endAdminSession(reason: "expired" | "logout" = "expired"): void {
  adminAuthChecked.value = true;
  if (!adminAuthed.value) return;
  adminSessionEpoch.value += 1;
  adminAuthed.value = false;
  adminUsername.value = null;
  window.dispatchEvent(new CustomEvent("orng-admin-signed-out", { detail: reason }));
}

export function resetAdminSessionForTests(): void {
  adminAuthed.value = false;
  adminAuthChecked.value = false;
  adminUsername.value = null;
  adminSessionEpoch.value = 0;
}
