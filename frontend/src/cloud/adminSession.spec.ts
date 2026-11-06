import { beforeEach, describe, expect, it } from "vitest";
import {
  adminAuthed,
  adminSessionEpoch,
  endAdminSession,
  markAdminAuthed,
  resetAdminSessionForTests,
} from "./adminSession";

describe("shared admin session", () => {
  beforeEach(() => {
    resetAdminSessionForTests();
    localStorage.clear();
  });

  it("one logout clears every shell that shares the session", () => {
    const seen: string[] = [];
    const onOut = (event: Event) => seen.push(String((event as CustomEvent).detail));
    window.addEventListener("orng-admin-signed-out", onOut);
    markAdminAuthed("ops");
    endAdminSession("logout");
    endAdminSession("logout");
    expect(adminAuthed.value).toBe(false);
    expect(adminSessionEpoch.value).toBe(1);
    expect(seen).toEqual(["logout"]);
    window.removeEventListener("orng-admin-signed-out", onOut);
  });

  it("keeps the scene draft when the session ends", () => {
    localStorage.setItem("orng-scene-draft:v2:loc", "{\"revision\":2}");
    markAdminAuthed("ops");
    endAdminSession("expired");
    expect(localStorage.getItem("orng-scene-draft:v2:loc")).toContain("revision");
  });

  it("does not replay a signed-out epoch", () => {
    markAdminAuthed("ops");
    const epoch = adminSessionEpoch.value;
    endAdminSession("expired");
    expect(adminSessionEpoch.value).toBe(epoch + 1);
    const after = adminSessionEpoch.value;
    endAdminSession("expired");
    expect(adminSessionEpoch.value).toBe(after);
  });
});
