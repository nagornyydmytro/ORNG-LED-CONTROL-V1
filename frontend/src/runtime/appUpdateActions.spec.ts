import { describe, expect, it } from "vitest";
import {
  appUpdateButtonState,
  applyConfirmText,
  selectionUnchanged,
  updateBadges,
} from "./appUpdateActions";

describe("update badges", () => {
  it("keeps available, downloaded, waiting, applied, and error apart", () => {
    expect(
      updateBadges({
        updateState: "idle",
        runningBuild: "a",
        desiredBuild: "b",
      }).available,
    ).toBe(true);
    expect(
      updateBadges({
        updateState: "staged",
        runningBuild: "a",
        desiredBuild: "b",
        stagedBuild: "b",
      }).downloaded,
    ).toBe(true);
    expect(updateBadges({ updateState: "restarting", desiredBuild: "b" }).waiting).toBe(true);
    expect(
      updateBadges({
        updateState: "healthy",
        runningBuild: "b",
        desiredBuild: "b",
      }).applied,
    ).toBe(true);
    expect(updateBadges({ updateState: "failed", lastError: "hash" }).error).toBe(true);
  });

  it("does not mark a successful check as downloaded or applied", () => {
    const badges = updateBadges({
      updateState: "idle",
      updatePhase: "available",
      runningBuild: "a",
      desiredBuild: "b",
      stagedBuild: "a",
    });
    expect(badges.downloaded).toBe(false);
    expect(badges.applied).toBe(false);
    expect(badges.available).toBe(true);
    const wrongPackage = updateBadges({
      updateState: "staged",
      runningBuild: "a",
      desiredBuild: "b",
      stagedBuild: "a",
    });
    expect(wrongPackage.downloaded).toBe(false);
    expect(wrongPackage.available).toBe(true);
  });
});

describe("update buttons", () => {
  it("requires managed mode, matching arch, a staged target, and a selected installation", () => {
    expect(
      appUpdateButtonState({
        hasInstallation: false,
        installMode: "managed",
        updateState: "staged",
        stagedBuild: "b",
        targetBuild: "b",
      }).apply,
    ).toBe(false);
    expect(
      appUpdateButtonState({
        hasInstallation: true,
        installMode: "developer",
        updateState: "staged",
        stagedBuild: "b",
        targetBuild: "b",
      }).apply,
    ).toBe(false);
    expect(
      appUpdateButtonState({
        hasInstallation: true,
        installMode: "managed",
        devicePlatform: "raspberry",
        deviceArch: "arm64",
        releasePlatform: "desktop_windows",
        releaseArch: "amd64",
        updateState: "staged",
        stagedBuild: "b",
        targetBuild: "b",
      }).stage,
    ).toBe(false);
    const ready = appUpdateButtonState({
      hasInstallation: true,
      installMode: "managed",
      devicePlatform: "raspberry",
      deviceArch: "arm64",
      releasePlatform: "raspberry",
      releaseArch: "arm64",
      updateState: "staged",
      stagedBuild: "b",
      targetBuild: "b",
    });
    expect(ready.apply).toBe(true);
    expect(ready.stage).toBe(true);
    expect(
      appUpdateButtonState({
        hasInstallation: true,
        installMode: "managed",
        updateState: "restarting",
        stagedBuild: "b",
        targetBuild: "b",
      }).apply,
    ).toBe(false);
    expect(
      appUpdateButtonState({
        hasInstallation: true,
        installMode: "managed",
        updateState: "staged",
        stagedBuild: "a",
        targetBuild: "b",
      }).apply,
    ).toBe(false);
  });
});

describe("restart confirmation", () => {
  it("names the captured installation and build", () => {
    const text = applyConfirmText({
      installationId: "inst-1",
      version: "0.2.0",
      buildId: "build-b",
    });
    expect(text).toContain("inst-1");
    expect(text).toContain("build-b");
    expect(selectionUnchanged("inst-1", "inst-1")).toBe(true);
    expect(selectionUnchanged("inst-1", "inst-2")).toBe(false);
  });
});
