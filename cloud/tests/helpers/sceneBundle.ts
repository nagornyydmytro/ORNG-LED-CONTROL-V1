/**
 * Full valid scene bundle fixtures for Worker tests (required root YAMLs).
 */

import { packBundle, type BundleFile } from "../../src/scenes/releases";

const enc = new TextEncoder();

/** Minimal allowlisted scene tree that unpackBundle / publishRelease accept. */
export function validSceneFiles(sceneId: string, extra: BundleFile[] = []): BundleFile[] {
  return [
    { path: "scene.yaml", data: enc.encode(`scene_id: ${sceneId}\nname: ${sceneId}\n`) },
    { path: "app.yaml", data: enc.encode("schema_version: 1\n") },
    { path: "patch.yaml", data: enc.encode("fixtures: []\n") },
    { path: "layout.yaml", data: enc.encode("fixtures: {}\n") },
    ...extra,
  ];
}

export async function packValidScene(
  sceneId: string,
  opts?: { appExtra?: string; extra?: BundleFile[] },
): Promise<{ bytes: Uint8Array; contentHash: string }> {
  const files = validSceneFiles(sceneId, opts?.extra ?? []);
  if (opts?.appExtra) {
    const idx = files.findIndex((f) => f.path === "app.yaml");
    if (idx >= 0) {
      files[idx] = {
        path: "app.yaml",
        data: enc.encode(`schema_version: 1\n${opts.appExtra}`),
      };
    }
  }
  return packBundle(files);
}

