import { afterEach, describe, expect, it, vi } from "vitest";
import { listPresets } from "../api/presets";
import {
  SceneUnavailableError,
  installScenePort,
  useCloudSceneBackend,
  useLocalSceneBackend,
  type ScenePort,
} from "./scenePort";

afterEach(() => {
  useLocalSceneBackend();
  installScenePort(null);
  vi.unstubAllGlobals();
});

describe("scene port readiness", () => {
  it("does not call the local API before a cloud port exists", async () => {
    useCloudSceneBackend();
    installScenePort(null);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(listPresets()).rejects.toBeInstanceOf(SceneUnavailableError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses the installed port instead of /api", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const port = {
      listPresets: vi.fn(async () => [{ id: "P01", label: "One", builtin: true }]),
    } as unknown as ScenePort;
    installScenePort(port);
    await expect(listPresets()).resolves.toEqual([{ id: "P01", label: "One", builtin: true }]);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
