import { beforeEach, describe, expect, it, vi } from "vitest";
import { installScenePort, sceneSyncPhase } from "./scenePort";

const { loadLocation, publishLocation } = vi.hoisted(() => ({
  loadLocation: vi.fn(),
  publishLocation: vi.fn(),
}));

vi.mock("../api/adminCloud", () => ({
  adminLoadLocationWorkspace: loadLocation,
  adminPublishLocationWorkspace: publishLocation,
  adminLoadSceneWorkspace: vi.fn(),
  adminPublishSceneWorkspace: vi.fn(),
}));

import {
  claimUnclaimedDraft,
  createCloudScenePort,
  draftRecoveryKey,
  draftStorageKey,
  legacyDraftStorageKey,
  setDraftBarrierForTests,
  unclaimedDraftKey,
} from "./cloudScenePort";

function workspace(sceneId = "orng-bar") {
  return {
    installation_id: "inst-1",
    scene_id: sceneId,
    version: 3,
    content_hash: "hash-3",
    track_latest: true,
    files: [
      { path: "scene.yaml", text: "name: Room\n" },
      { path: "app.yaml", text: "transport: mock\n" },
      { path: "patch.yaml", text: "fixtures: []\n" },
      { path: "layout.yaml", text: "placements: []\n" },
      {
        path: "presets/A01.yaml",
        text: "id: A01\nlabel: One\nbuiltin: true\nhardware_tuned: false\n",
      },
    ],
  };
}

function port(account: string, location: string, scene = "orng-bar") {
  return createCloudScenePort({
    installationId: "inst-1",
    locationId: location,
    sceneId: scene,
    accountId: account,
    live: () => ({}),
  });
}

beforeEach(() => {
  localStorage.clear();
  setDraftBarrierForTests(null);
  installScenePort(null);
  loadLocation.mockReset();
  publishLocation.mockReset();
  loadLocation.mockResolvedValue(workspace());
  publishLocation.mockResolvedValue({
    ok: true,
    status: "publishing",
    scene_id: "orng-bar",
    version: 4,
    content_hash: "hash-4",
    idempotent: false,
    command_id: "cmd",
    sync_status: "waiting",
    sync_error: null,
  });
});

describe("scene draft isolation", () => {
  it("does not invent an ORNG BAR scene for a location that has not loaded one", async () => {
    loadLocation.mockRejectedValueOnce(new Error("no_release"));
    const editor = createCloudScenePort({
      installationId: "inst-foreign",
      locationId: "loc-new",
      sceneId: null,
      accountId: "alice",
      live: () => ({}),
    });
    await expect(editor.publishForLocation()).rejects.toThrow(/no_release|сцени/);
    expect(publishLocation).not.toHaveBeenCalled();
    expect(Object.keys(localStorage).join(" ")).not.toContain("orng-bar");
  });

  it("does not publish a look the current fixtures cannot play", async () => {
    const base = workspace("room-test");
    loadLocation.mockResolvedValue({
      ...base,
      scene_id: "room-test",
      files: [
        { path: "scene.yaml", text: "name: Room\n" },
        { path: "app.yaml", text: "transport: mock\n" },
        {
          path: "patch.yaml",
          text: "fixtures:\n  - {id: par_1, kind: par, profile_id: par, groups: [par, all_rear]}\n",
        },
        { path: "layout.yaml", text: "placements: []\n" },
        {
          path: "profiles/par.yaml",
          text: "kind: par\nchannels:\n  - {local: 1, role: dimmer}\n",
        },
        {
          path: "presets/P01.yaml",
          text: "episodes:\n  - {id: beam, groups: [beam], effect: static}\n",
        },
      ],
    });
    const editor = port("alice", "loc-test", "room-test");
    await expect(editor.publishForLocation()).rejects.toThrow(/не має приладів/);
    expect(publishLocation).not.toHaveBeenCalled();
  });

  it("keeps an empty episode list as a draft", async () => {
    const base = workspace("room-test");
    loadLocation.mockResolvedValue({
      ...base,
      scene_id: "room-test",
      files: base.files.map((file) =>
        file.path === "presets/A01.yaml"
          ? { ...file, text: "id: A01\nlabel: One\nepisodes: []\n" }
          : file,
      ),
    });
    const editor = port("alice", "loc-test", "room-test");
    await expect(editor.publishForLocation()).rejects.toThrow(/епізоди мають бути списком/);
    expect(publishLocation).not.toHaveBeenCalled();
  });

  it("parks an ownerless legacy draft instead of giving it to the signed-in account", async () => {
    const legacy = legacyDraftStorageKey("loc-a");
    const files = workspace().files.map((file) => [file.path, file.text] as [string, string]);
    files[1] = ["app.yaml", "transport: mock\nnote: legacy-keep\n"];
    localStorage.setItem(
      legacy,
      JSON.stringify({
        sceneId: "orng-bar",
        version: 3,
        revision: 2,
        files,
      }),
    );
    const alice = port("alice", "loc-a");
    expect(await alice.fetchAppConfig()).not.toMatchObject({ note: "legacy-keep" });
    expect(localStorage.getItem(legacy)).toBeNull();
    const parked = unclaimedDraftKey("loc-a", "orng-bar");
    expect(localStorage.getItem(parked)).toContain("legacy-keep");
    expect(localStorage.getItem(draftStorageKey("alice", "loc-a", "orng-bar"))).toBeNull();
    expect(localStorage.getItem(draftStorageKey("bob", "loc-a", "orng-bar"))).toBeNull();
    expect(claimUnclaimedDraft("bob", "loc-a", "orng-bar")).toBe(true);
    expect(localStorage.getItem(parked)).toBeNull();
    expect(await port("bob", "loc-a").fetchAppConfig()).toMatchObject({ note: "legacy-keep" });
    expect(claimUnclaimedDraft("alice", "loc-a", "orng-bar")).toBe(false);
    expect(localStorage.getItem(draftStorageKey("alice", "loc-a", "orng-bar"))).toBeNull();
  });

  it("keeps drafts apart across accounts, scenes and locations", async () => {
    const alice = port("alice", "loc-a", "orng-bar");
    await alice.saveAppConfig({ transport: "mock", note: "alice" });
    const bob = port("bob", "loc-a", "orng-bar");
    expect(await bob.fetchAppConfig()).not.toMatchObject({ note: "alice" });
    const otherScene = port("alice", "loc-a", "other-room");
    loadLocation.mockResolvedValueOnce(workspace("other-room"));
    expect(await otherScene.fetchAppConfig()).not.toMatchObject({ note: "alice" });
    const otherPlace = port("alice", "loc-b", "orng-bar");
    expect(await otherPlace.fetchAppConfig()).not.toMatchObject({ note: "alice" });
    expect(localStorage.getItem(draftStorageKey("alice", "loc-a", "orng-bar"))).toContain("alice");
  });

  it("restores the same account after logout and does not give the draft to the next account", async () => {
    const alice = port("alice", "loc-a");
    await alice.saveAppConfig({ transport: "mock", note: "keep" });
    installScenePort(null);
    await alice.saveAppConfig({ transport: "mock", note: "after-logout" });
    expect(localStorage.getItem(draftStorageKey("alice", "loc-a", "orng-bar"))).toContain("keep");
    expect(localStorage.getItem(draftStorageKey("alice", "loc-a", "orng-bar"))).not.toContain(
      "after-logout",
    );
    const returned = port("alice", "loc-a");
    expect(await returned.fetchAppConfig()).toMatchObject({ note: "keep" });
    const next = port("bob", "loc-a");
    expect(await next.fetchAppConfig()).not.toMatchObject({ note: "keep" });
  });

  it("does not let a second tab overwrite a newer draft", async () => {
    const first = port("alice", "loc-a");
    const second = port("alice", "loc-a");
    await first.saveAppConfig({ transport: "mock", note: "first" });
    await second.saveAppConfig({ transport: "mock", note: "second" });
    const key = draftStorageKey("alice", "loc-a", "orng-bar");
    const saved = JSON.parse(localStorage.getItem(key) ?? "{}") as {
      revision?: number;
      files?: Array<[string, string]>;
    };
    localStorage.setItem(
      key,
      JSON.stringify({ ...saved, revision: (saved.revision ?? 1) + 4 }),
    );
    await second.saveAppConfig({ transport: "mock", note: "lost" });
    expect(sceneSyncPhase.value).toBe("conflict");
    expect(localStorage.getItem(key)).not.toContain("lost");
  });

  it("keeps an edit that arrives while publish is in flight", async () => {
    let release: (value: unknown) => void = () => undefined;
    publishLocation.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const editor = port("alice", "loc-a");
    await editor.saveAppConfig({ transport: "mock", note: "before" });
    const pending = editor.publishForLocation();
    await Promise.resolve();
    await editor.saveAppConfig({ transport: "mock", note: "during" });
    release({
      version: 4,
      content_hash: "hash-4",
    });
    await pending;
    expect(sceneSyncPhase.value).toBe("draft");
    expect(localStorage.getItem(draftStorageKey("alice", "loc-a", "orng-bar"))).toContain("during");
  });

  it("keeps the draft on a version conflict and when the network is down", async () => {
    const editor = port("alice", "loc-a");
    await editor.saveAppConfig({ transport: "mock", note: "local" });
    publishLocation.mockRejectedValueOnce(new Error("409 version_conflict"));
    await expect(editor.publishForLocation()).rejects.toThrow(/409/);
    expect(sceneSyncPhase.value).toBe("conflict");
    expect(localStorage.getItem(draftStorageKey("alice", "loc-a", "orng-bar"))).toContain("local");

    sceneSyncPhase.value = "draft";
    publishLocation.mockRejectedValueOnce(new Error("Failed to fetch"));
    await expect(editor.publishForLocation()).rejects.toThrow(/fetch/);
    expect(sceneSyncPhase.value).toBe("failed");
    expect(localStorage.getItem(draftStorageKey("alice", "loc-a", "orng-bar"))).toContain("local");
  });

  it("keeps the other tab's newer draft when an older publish returns", async () => {
    let release: (value: unknown) => void = () => undefined;
    publishLocation.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const tabA = port("alice", "loc-a");
    const tabB = port("alice", "loc-a");
    await tabA.saveAppConfig({ transport: "mock", note: "from-a" });
    const pending = tabA.publishForLocation();
    await Promise.resolve();
    await tabB.saveAppConfig({ transport: "mock", note: "from-b" });
    release({ version: 4, content_hash: "hash-4" });
    await pending;
    const key = draftStorageKey("alice", "loc-a", "orng-bar");
    expect(localStorage.getItem(key)).toContain("from-b");
    expect(localStorage.getItem(key)).not.toContain("from-a");
    installScenePort(null);
    expect(await port("alice", "loc-a").fetchAppConfig()).toMatchObject({ note: "from-b" });
  });

  it("does not drop either side of two overlapping saves", async () => {
    const waits: Array<() => void> = [];
    let hits = 0;
    setDraftBarrierForTests(
      () =>
        new Promise((resolve) => {
          hits += 1;
          if (hits <= 3) waits.push(resolve);
          else resolve();
        }),
    );
    const tabA = port("alice", "loc-a");
    const tabB = port("alice", "loc-a");
    const first = tabA.saveAppConfig({ transport: "mock", note: "overlap-a" });
    const second = tabB.saveAppConfig({ transport: "mock", note: "overlap-b" });
    await vi.waitFor(() => expect(waits.length).toBe(2));
    waits[0]!();
    await vi.waitFor(() => expect(waits.length).toBe(3));
    waits[1]!();
    waits[2]!();
    await Promise.all([first, second]);
    setDraftBarrierForTests(null);
    const key = draftStorageKey("alice", "loc-a", "orng-bar");
    const canonical = localStorage.getItem(key) ?? "";
    const recovery = localStorage.getItem(draftRecoveryKey("alice", "loc-a", "orng-bar")) ?? "";
    const notes = `${canonical}\n${recovery}`;
    expect(notes).toContain("overlap-a");
    expect(notes).toContain("overlap-b");
    expect([canonical.includes("overlap-a"), canonical.includes("overlap-b")].filter(Boolean)).toHaveLength(1);
    installScenePort(null);
    const reloaded = String((await port("alice", "loc-a").fetchAppConfig()).note);
    expect(["overlap-a", "overlap-b"]).toContain(reloaded);
  });

  it("keeps musical actions through a draft save and a reload", async () => {
    const editor = port("alice", "loc-test", "room-test");
    await editor.createCustomPreset("M01", "Музичний");
    await editor.savePreset("M01", {
      schema_version: 1,
      id: "M01",
      label: "Музичний",
      hardware_tuned: false,
      builtin: false,
      musical_version: 1,
      episodes: [
        {
          id: "short",
          duration_s: 4,
          groups: ["par"],
          palette: "warm_orange",
          effect: "pulse",
          speed: 0.5,
          intensity: 0.8,
          transition: "cut",
          actions: [
            { id: "a1", groups: ["par"], effect: "pulse", speed: 0.5, intensity: 0.8, cycle_beats: 1 },
            { id: "a2", groups: ["bar"], effect: "chase", speed: 0.5, intensity: 0.7, cycle_beats: 2 },
            { id: "a4", groups: ["left"], effect: "wave", speed: 0.4, intensity: 0.6, cycle_beats: 4 },
            { id: "a8", groups: ["beam"], effect: "breathe", speed: 0.3, intensity: 0.5, cycle_beats: 8 },
          ],
        },
        {
          id: "long",
          duration_s: 11,
          groups: ["bar"],
          palette: "cool_blue",
          effect: "wave",
          speed: 0.4,
          intensity: 0.6,
          transition: "soft",
          cycle_beats: 4,
        },
      ],
    });
    installScenePort(null);
    const loaded = await port("alice", "loc-test", "room-test").getPreset("M01");
    expect(loaded.musical_version).toBe(1);
    expect(loaded.episodes.map((episode) => episode.duration_s)).toEqual([4, 11]);
    expect(loaded.episodes[0]?.actions?.map((action) => action.cycle_beats)).toEqual([1, 2, 4, 8]);
  });

  it("validates DMX range from footprint and rejects a missing pad preset", async () => {
    const base = workspace("room-test");
    loadLocation.mockResolvedValue({
      ...base,
      files: [
        ...base.files.filter((file) => file.path !== "app.yaml"),
        { path: "app.yaml", text: "pad_presets:\n  - GONE\n" },
        {
          path: "profiles/bar.yaml",
          text: "id: bar\nkind: bar\nfootprint: 15\nchannels:\n  - {local: 1, role: dimmer}\n  - {local: 3, role: dimmer}\n",
        },
      ],
    });
    const result = await port("alice", "loc-test", "room-test").validatePatch({
      fixtures: [
        { id: "a", profile_id: "bar", kind: "bar", start_address: 1 },
        { id: "b", profile_id: "bar", kind: "bar", start_address: 10 },
      ],
    });
    expect(result.ok).toBe(false);
    expect(result.errors).toContain("a і b перетинаються");
    expect(result.errors).toContain("слот пульта GONE не має пресета");
  });
});
