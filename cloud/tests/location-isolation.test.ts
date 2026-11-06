/**
 * Two locations with the same internal names stay separate documents.
 * A shared scene_id is one document on purpose. A foreign scene_id is refused.
 */

import { describe, expect, it } from "vitest";
import { listOperatorLocations } from "../src/db/locations";
import {
  mergeShowPrefs,
  readShowPrefs,
  showPrefsKey,
  writeShowPrefs,
} from "../src/locations/showPrefs";
import {
  ReleaseError,
  assertDownloadAllowed,
  packBundle,
  unpackBundle,
  validateSceneSemantics,
} from "../src/scenes/releases";
import {
  loadLocationWorkspace,
  publishAssignedWorkspace,
  publishLocationWorkspace,
} from "../src/scenes/workspace";
import { createMemoryEnv } from "./helpers/memoryEnv";

const enc = new TextEncoder();

function filesFor(
  sceneId: string,
  opts: { address: number; pan: number; label: string },
): Array<{ path: string; text: string }> {
  return [
    { path: "scene.yaml", text: `scene_id: ${sceneId}\nname: ${sceneId}\n` },
    { path: "app.yaml", text: "schema_version: 1\n" },
    {
      path: "patch.yaml",
      text:
        "fixtures:\n" +
        "  - id: par_1\n" +
        "    profile_id: profile_1\n" +
        "    kind: par\n" +
        `    start_address: ${opts.address}\n` +
        "    spatial:\n" +
        `      pan_offset: ${opts.pan}\n`,
    },
    { path: "layout.yaml", text: "fixtures: {}\n" },
    {
      path: "profiles/profile_1.yaml",
      text: "kind: par\nfootprint: 1\nchannels:\n  - local: 1\n    role: dimmer\n",
    },
    { path: "presets/P01.yaml", text: `id: P01\nlabel: ${opts.label}\n` },
  ];
}

function seedLocation(
  env: ReturnType<typeof createMemoryEnv>,
  id: string,
  sceneId: string,
  executor: string | null = null,
) {
  env.__db.tables.locations.push({
    id,
    name: id,
    scene_id: sceneId,
    executor_installation_id: executor,
  });
}

async function publish(
  env: ReturnType<typeof createMemoryEnv>,
  locationId: string,
  sceneId: string,
  opts: { address: number; pan: number; label: string },
  baseVersion: number | null,
) {
  return publishLocationWorkspace(env, {
    locationId,
    sceneId,
    sceneName: sceneId,
    baseVersion,
    files: filesFor(sceneId, opts),
    actor: "isolation-test",
    executorInstallationId: null,
  });
}

describe("location isolation", () => {
  it("keeps identical internal names inside their own scene", async () => {
    const env = createMemoryEnv();
    seedLocation(env, "loc-a", "scene-a");
    seedLocation(env, "loc-b", "scene-b");
    const publishedA = await publish(env, "loc-a", "scene-a", {
      address: 20,
      pan: 0.2,
      label: "A-red",
    }, null);
    const publishedB = await publish(env, "loc-b", "scene-b", {
      address: 100,
      pan: -0.2,
      label: "B-blue",
    }, null);
    expect(publishedA.content_hash).not.toBe(publishedB.content_hash);

    const beforeB = await loadLocationWorkspace(env, "loc-b");
    const hashB = beforeB.content_hash;
    const textB = beforeB.files.map((file) => `${file.path}\n${file.text}`).join("\n");

    const again = await publish(env, "loc-a", "scene-a", {
      address: 21,
      pan: 0.25,
      label: "A-red-edited",
    }, 1);
    expect(again.content_hash).not.toBe(publishedA.content_hash);

    const afterA = await loadLocationWorkspace(env, "loc-a");
    const afterB = await loadLocationWorkspace(env, "loc-b");
    expect(afterA.files.find((file) => file.path === "presets/P01.yaml")?.text).toContain(
      "A-red-edited",
    );
    expect(afterA.files.find((file) => file.path === "patch.yaml")?.text).toContain(
      "start_address: 21",
    );
    expect(afterB.content_hash).toBe(hashB);
    expect(afterB.files.map((file) => `${file.path}\n${file.text}`).join("\n")).toBe(textB);
    expect(afterB.files.find((file) => file.path === "presets/P01.yaml")?.text).toContain("B-blue");
    expect(afterB.scene_id).toBe("scene-b");
  });

  it("refuses a body scene that the location does not own", async () => {
    const env = createMemoryEnv();
    seedLocation(env, "loc-a", "scene-a");
    seedLocation(env, "loc-b", "scene-b");
    await publish(env, "loc-a", "scene-a", { address: 20, pan: 0.2, label: "A-red" }, null);
    await publish(env, "loc-b", "scene-b", { address: 100, pan: -0.2, label: "B-blue" }, null);
    const hashB = (await loadLocationWorkspace(env, "loc-b")).content_hash;
    await expect(
      publishLocationWorkspace(env, {
        locationId: "loc-a",
        sceneId: "scene-b",
        sceneName: "scene-b",
        baseVersion: 1,
        files: filesFor("scene-b", { address: 100, pan: -0.2, label: "stolen" }),
        actor: "isolation-test",
        executorInstallationId: null,
      }),
    ).rejects.toMatchObject({ code: "scene_not_owned", status: 409 });
    expect((await loadLocationWorkspace(env, "loc-b")).content_hash).toBe(hashB);
    expect((await loadLocationWorkspace(env, "loc-a")).files.find((f) => f.path === "presets/P01.yaml")?.text).toContain(
      "A-red",
    );
  });

  it("treats one scene_id on two locations as one shared document", async () => {
    const env = createMemoryEnv();
    seedLocation(env, "loc-a", "scene-shared");
    seedLocation(env, "loc-b", "scene-shared");
    const published = await publish(env, "loc-a", "scene-shared", {
      address: 30,
      pan: 0.1,
      label: "shared-P01",
    }, null);
    const loadedB = await loadLocationWorkspace(env, "loc-b");
    expect(loadedB.scene_id).toBe("scene-shared");
    expect(loadedB.content_hash).toBe(published.content_hash);
    expect(loadedB.files.find((file) => file.path === "presets/P01.yaml")?.text).toContain(
      "shared-P01",
    );
  });

  it("does not retarget an installation onto another scene", async () => {
    const env = createMemoryEnv();
    env.__db.tables.scene_assignments.push({
      installation_id: "inst-a",
      scene_id: "scene-a",
      desired_version: 1,
      track_latest: 1,
      updated_at: "2020-01-01T00:00:00.000Z",
    });
    await expect(
      publishAssignedWorkspace(env, {
        installationId: "inst-a",
        sceneId: "scene-b",
        sceneName: "scene-b",
        baseVersion: null,
        files: filesFor("scene-b", { address: 100, pan: -0.2, label: "B-blue" }),
        actor: "isolation-test",
      }),
    ).rejects.toBeInstanceOf(ReleaseError);
    expect(env.__db.tables.scene_releases.filter((row) => row.scene_id === "scene-b")).toHaveLength(0);
    expect(env.__db.tables.scene_assignments[0]?.scene_id).toBe("scene-a");
  });

  it("does not attach a device just because the scene id matches", async () => {
    const env = createMemoryEnv();
    seedLocation(env, "loc-a", "scene-a", "exec-a");
    seedLocation(env, "loc-b", "scene-b", null);
    const row = (id: string, locationId: string | null) => ({
      id,
      name: id,
      platform: "raspberry",
      revoked_at: null,
      created_at: "2020-01-01T00:00:00.000Z",
      location_id: locationId,
      capabilities_json: "{}",
    });
    env.__db.tables.installations.push(
      row("exec-a", null),
      row("member-b", "loc-b"),
      row("same-scene", null),
    );
    env.__db.tables.scene_assignments.push(
      {
        installation_id: "member-b",
        scene_id: "scene-a",
        desired_version: 1,
        track_latest: 1,
        updated_at: "2020-01-01T00:00:00.000Z",
      },
      {
        installation_id: "same-scene",
        scene_id: "scene-a",
        desired_version: 1,
        track_latest: 1,
        updated_at: "2020-01-01T00:00:00.000Z",
      },
    );
    const locations = await listOperatorLocations(env);
    const a = locations.find((item) => item.id === "loc-a");
    const b = locations.find((item) => item.id === "loc-b");
    expect(a?.installation_id).toBe("exec-a");
    expect(a?.installations.map((item) => item.id)).toEqual(["exec-a"]);
    expect(b?.installations.map((item) => item.id)).toEqual(["member-b"]);
    expect(b?.installation_id).toBe("member-b");
  });

  it("requires the device scope and the assignment, not a bare id", async () => {
    const env = createMemoryEnv();
    env.__db.tables.scene_assignments.push({
      installation_id: "inst-a",
      scene_id: "scene-a",
      desired_version: 1,
      track_latest: 1,
      updated_at: "2020-01-01T00:00:00.000Z",
    });
    await expect(
      assertDownloadAllowed(env, {
        installationId: "inst-a",
        sceneId: "scene-b",
        version: 1,
        scopes: ["scene:read_assigned"],
        revoked: false,
      }),
    ).rejects.toMatchObject({ status: 403 });
    await expect(
      assertDownloadAllowed(env, {
        installationId: "inst-a",
        sceneId: "scene-a",
        version: 1,
        scopes: [],
        revoked: false,
      }),
    ).rejects.toMatchObject({ status: 403 });
    await expect(
      assertDownloadAllowed(env, {
        installationId: "inst-a",
        sceneId: "scene-a",
        version: 1,
        scopes: ["scene:read_assigned"],
        revoked: true,
      }),
    ).rejects.toMatchObject({ status: 403, code: "revoked" });
    await assertDownloadAllowed(env, {
      installationId: "inst-a",
      sceneId: "scene-a",
      version: 1,
      scopes: ["scene:read_assigned"],
      revoked: false,
    });
  });

  it("rejects a template that carries secrets, an executor, or another scene", async () => {
    const clean = filesFor("scene-a", { address: 20, pan: 0.2, label: "A-red" });
    expect(clean.map((file) => file.text).join("\n")).not.toContain("orng-bar");
    expect(validateSceneSemantics(clean.map((file) => ({ path: file.path, data: enc.encode(file.text) })))).toEqual(
      [],
    );
    const missingProfile = clean.filter((file) => file.path !== "profiles/profile_1.yaml");
    expect(
      validateSceneSemantics(
        missingProfile.map((file) => ({ path: file.path, data: enc.encode(file.text) })),
      ).join(" "),
    ).toContain("невідомий профіль");
    const withToken = clean.map((file) =>
      file.path === "app.yaml" ? { ...file, text: "schema_version: 1\ntoken: local-secret\n" } : file,
    );
    expect(
      validateSceneSemantics(withToken.map((file) => ({ path: file.path, data: enc.encode(file.text) }))).join(
        " ",
      ),
    ).toContain("token");
    const withExecutor = clean.map((file) =>
      file.path === "app.yaml"
        ? { ...file, text: "schema_version: 1\nexecutor_installation_id: old-executor\n" }
        : file,
    );
    expect(
      validateSceneSemantics(
        withExecutor.map((file) => ({ path: file.path, data: enc.encode(file.text) })),
      ).join(" "),
    ).toContain("executor_installation_id");
    const packed = await packBundle(
      filesFor("orng-bar", { address: 1, pan: 0, label: "bar" }).map((file) => ({
        path: file.path,
        data: enc.encode(file.text),
      })),
    );
    await expect(unpackBundle(packed.bytes, "scene-a")).rejects.toMatchObject({
      code: "scene_id_mismatch",
    });
  });
});

describe("show prefs isolation", () => {
  it("reads without moving updated_at and merges fields independently", async () => {
    const env = createMemoryEnv();
    const saved = await writeShowPrefs(env, "loc-a", {
      mode: "bar",
      scope: "preset",
      palette: "orange",
      enabled: true,
      bpm: 128,
      phase: 0.5,
      holds: { strobe: true },
      device_id: "USB Audio",
      network: "10.0.0.8",
      credentials: "secret",
    });
    const stored = await (await env.SCENE_RELEASES.get(showPrefsKey("loc-a")))?.text();
    expect(stored).not.toContain("128");
    expect(stored).not.toContain("USB");
    expect(stored).not.toContain("10.0.0.8");
    expect(stored).not.toContain("secret");
    expect(stored).not.toContain("enabled");
    const stamped = saved.updated_at;
    expect(stamped).toBeTruthy();
    const read = await readShowPrefs(env, "loc-a");
    expect(read?.updated_at).toBe(stamped);
    expect(await (await env.SCENE_RELEASES.get(showPrefsKey("loc-a")))?.text()).toBe(stored);

    const same = await writeShowPrefs(env, "loc-a", { palette: "orange" });
    expect(same.updated_at).toBe(stamped);
    expect(await (await env.SCENE_RELEASES.get(showPrefsKey("loc-a")))?.text()).toBe(stored);

    const base = { mode: saved.mode, scope: saved.scope, palette: saved.palette };
    const modeWrite = await writeShowPrefs(env, "loc-a", { mode: "beat", base });
    const paletteWrite = await writeShowPrefs(env, "loc-a", { palette: "blue", base });
    expect(paletteWrite.mode).toBe("beat");
    expect(paletteWrite.palette).toBe("blue");
    expect(paletteWrite.conflicts).toEqual([]);
    expect(modeWrite.mode).toBe("beat");

    const conflict = await writeShowPrefs(env, "loc-a", { palette: "red", base });
    expect(conflict.palette).toBe("blue");
    expect(conflict.conflicts).toEqual(["palette"]);
    expect(conflict.updated_at).toBe(paletteWrite.updated_at);

    const other = await writeShowPrefs(env, "loc-b", { mode: "smooth", palette: "violet" });
    expect(other.palette).toBe("violet");
    const afterA = await readShowPrefs(env, "loc-a");
    expect(afterA?.palette).toBe("blue");
    expect(afterA?.mode).toBe("beat");
    expect(await readShowPrefs(env, "loc-b")).toMatchObject({ palette: "violet" });
  });

  it("defines a same-field conflict against the snapshot that was read", () => {
    const current = {
      mode: "smooth" as const,
      scope: "preset" as const,
      palette: "blue" as const,
      cast: "all" as const,
      character: "rhythmic" as const,
      intensity: "full" as const,
      period: "auto",
      par_rhythm: false,
      accent: 0,
      updated_at: "t1",
    };
    const merged = mergeShowPrefs(current, {
      palette: "red",
      base: { mode: "smooth", scope: "preset", palette: "orange" },
    });
    expect(merged.changed).toBe(false);
    expect(merged.conflicts).toEqual(["palette"]);
    expect(merged.next.updated_at).toBe("t1");
  });
});
