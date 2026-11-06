import { handleAdminLogin, handleAdminLogout, handleAdminMe } from "../auth/login";
import { localBypassAllowed, requireCsrf, resolveAdminSession } from "../auth/session";
import { adminCorsHeaders, rejectBadOrigin } from "../auth/origin";
import { listAudit, writeAudit } from "../db/audit";
import { listCommands } from "../db/commands";
import { getInstallation, listInstallations, publicInstallation } from "../db/installations";
import { listOperatorLocations } from "../db/locations";
import { deviceStub, forwardAdminWs } from "../devices";
import type { Env } from "../env";
import { errorJson, json, uuid } from "../env";
import {
  PairingError,
  approvePairing,
  initiateTokenRotation,
  revokeInstallation,
} from "../pairing";
import type { Platform } from "../db/installations";
import {
  AppReleaseError,
  assignAppRelease,
  bulkAppRollout,
  listAppReleases,
  listAppUpdateEvents,
  publicAppAssignment,
  publishAppRelease,
  rollbackAppAssignment,
} from "../app/releases";
import { buildSigningManifest, type AppSigningManifest } from "../app/signing";
import {
  ReleaseError,
  assignScene,
  buildControlSurface,
  getAssignment,
  getReleaseBundle,
  listReleases,
  listScenes,
  publishRelease,
} from "../scenes/releases";
import { isAllowedCommandType } from "../protocol/types";
import { loadAssignedWorkspace, loadLocationWorkspace, publishAssignedWorkspace, publishLocationWorkspace } from "../scenes/workspace";
import { readShowPrefs, writeShowPrefs } from "../locations/showPrefs";
import { autoCatalogPayload, previewFromWorkspace } from "../scenes/autoPreview";
import { sha256Bytes } from "../protocol/crypto";

const PLATFORMS = new Set([
  "raspberry",
  "desktop_windows",
  "desktop_macos",
  "development",
]);

export async function handleAdminRequest(request: Request, env: Env): Promise<Response> {
  const cors = adminCorsHeaders(env, request);
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: cors });
  }

  const badOrigin = rejectBadOrigin(env, request);
  if (badOrigin) return withCors(badOrigin, cors);

  const url = new URL(request.url);
  const path = url.pathname;

  // Public SPA shell (login form lives in the UI). APIs / admin WS require session.
  if (request.method === "GET" && isAdminSpaPath(path)) {
    if (env.ASSETS) {
      const rewriteToIndex =
        path === "/admin" ||
        path === "/admin/" ||
        (path !== "/" && !path.startsWith("/assets/") && !path.includes("."));
      const assetReq = rewriteToIndex
        ? new Request(new URL("/", url).toString(), request)
        : request;
      const assetRes = await env.ASSETS.fetch(assetReq);
      const headers = new Headers(assetRes.headers);
      const type = headers.get("content-type") ?? "";
      if (rewriteToIndex || path === "/" || type.includes("text/html")) {
        headers.set("cache-control", "no-store");
      }
      return withCors(
        new Response(assetRes.body, { status: assetRes.status, headers }),
        cors,
      );
    }
    if (path === "/" || path === "/admin" || path === "/admin/") {
      return withCors(
        new Response(LOCAL_ADMIN_FALLBACK_HTML, {
          headers: { "content-type": "text/html; charset=utf-8" },
        }),
        cors,
      );
    }
  }

  // Auth endpoints (login is public; logout/me need session where noted).
  if (path === "/admin/auth/login" && request.method === "POST") {
    return withCors(await handleAdminLogin(request, env), cors);
  }
  if (path === "/admin/auth/logout" && request.method === "POST") {
    const session = await resolveAdminSession(request, env);
    // Allow logout even with expired cookie to clear browser state.
    void session;
    const csrf = requireCsrf(request);
    // Logout without CSRF if already unauthenticated (clear cookies anyway).
    if (session.ok) {
      if (csrf) return withCors(csrf, cors);
    }
    return withCors(await handleAdminLogout(request, env), cors);
  }

  const session = await resolveAdminSession(request, env);
  if (!session.ok) return withCors(session.response, cors);
  const actor = session.identity.email;
  void localBypassAllowed;

  if (path === "/admin/auth/me" && request.method === "GET") {
    return withCors(handleAdminMe(session.identity.username), cors);
  }

  // CSRF required in real sessions; local DEV bypass skips (same-origin tooling / unit tests).
  if (!localBypassAllowed(env, request)) {
    const csrfFail = requireCsrf(request);
    if (csrfFail) return withCors(csrfFail, cors);
  }

  try {
    if (path === "/admin/devices" && request.method === "GET") {
      const rows = await listInstallations(env);
      const devices = await Promise.all(
        rows.map(async (row) => {
          let online = false;
          let rotationPending = false;
          try {
            const stub = deviceStub(env, row.id);
            const statusRes = await stub.fetch("https://device-do/status");
            const live = (await statusRes.json()) as { online?: boolean; device_connected?: boolean };
            online = Boolean(live.online ?? live.device_connected);
          } catch {
            online = false;
          }
          const cred = await env.DB.prepare(
            `SELECT rotation_pending FROM device_credentials WHERE installation_id = ?`,
          )
            .bind(row.id)
            .first<{ rotation_pending: number }>();
          rotationPending = Boolean(cred?.rotation_pending);
          return publicInstallation(row, { online, rotationPending });
        }),
      );
      return withCors(json({ devices }), cors);
    }
    const locationWorkspace = /^\/admin\/locations\/([^/]+)\/scene-workspace$/.exec(path);
    if (locationWorkspace && request.method === "GET") {
      const locationId = decodeURIComponent(locationWorkspace[1]!);
      const workspace = await loadLocationWorkspace(env, locationId);
      return withCors(json(workspace), cors);
    }
    if (locationWorkspace && request.method === "POST") {
      const locationId = decodeURIComponent(locationWorkspace[1]!);
      const body = (await request.json()) as {
        scene_id?: string;
        scene_name?: string;
        base_version?: number | null;
        files?: Array<{ path?: string; text?: string }>;
      };
      if (!body.scene_id || !Array.isArray(body.files) || body.files.length === 0) {
        return withCors(errorJson(400, "bad_request", "scene_id and files required"), cors);
      }
      const locations = await listOperatorLocations(env);
      const location = locations.find((item) => item.id === locationId);
      if (!location) {
        return withCors(errorJson(404, "not_found", "Location not found"), cors);
      }
      if (location.scene_id !== body.scene_id) {
        return withCors(
          errorJson(409, "scene_not_owned", "Location does not own this scene"),
          cors,
        );
      }
      const published = await publishLocationWorkspace(env, {
        locationId,
        sceneId: body.scene_id,
        sceneName: body.scene_name || location?.name || body.scene_id,
        baseVersion: body.base_version ?? null,
        files: body.files.map((file) => ({
          path: String(file.path ?? ""),
          text: String(file.text ?? ""),
        })),
        actor,
        executorInstallationId: location?.installation_id ?? null,
      });
      return withCors(json(published), cors);
    }

    const locationPrefs = /^\/admin\/locations\/([^/]+)\/show-prefs$/.exec(path);
    if (locationPrefs && (request.method === "GET" || request.method === "PUT")) {
      const locationId = decodeURIComponent(locationPrefs[1]!);
      const locations = await listOperatorLocations(env);
      if (!locations.some((item) => item.id === locationId)) {
        return withCors(errorJson(404, "not_found", "Location not found"), cors);
      }
      if (request.method === "GET") {
        return withCors(json({ prefs: await readShowPrefs(env, locationId) }), cors);
      }
      const body = (await request.json()) as unknown;
      const { conflicts, ...prefs } = await writeShowPrefs(env, locationId, body);
      await writeAudit(env, actor, "location_show_prefs", locationId, { ...prefs });
      return withCors(json({ prefs, conflicts }), cors);
    }

    const locationAutoCatalog = /^\/admin\/locations\/([^/]+)\/auto\/catalog$/.exec(path);
    if (locationAutoCatalog && request.method === "GET") {
      const locationId = decodeURIComponent(locationAutoCatalog[1]!);
      const locations = await listOperatorLocations(env);
      if (!locations.some((item) => item.id === locationId)) {
        return withCors(errorJson(404, "not_found", "Location not found"), cors);
      }
      return withCors(json(autoCatalogPayload()), cors);
    }
    const locationAutoPreview = /^\/admin\/locations\/([^/]+)\/auto\/preview$/.exec(path);
    if (locationAutoPreview && request.method === "GET") {
      const locationId = decodeURIComponent(locationAutoPreview[1]!);
      const locations = await listOperatorLocations(env);
      const location = locations.find((item) => item.id === locationId);
      if (!location) {
        return withCors(errorJson(404, "not_found", "Location not found"), cors);
      }
      let sceneId: string | null = location.scene_id ?? null;
      let sceneName: string | null = location.name ?? null;
      let files: Array<{ path: string; text: string }> | null = null;
      try {
        const workspace = await loadLocationWorkspace(env, locationId);
        sceneId = workspace.scene_id ?? sceneId;
        sceneName = workspace.scene_name ?? sceneName;
        files = workspace.files.map((file) => ({ path: file.path, text: file.text }));
      } catch {
        files = null;
      }
      const look = url.searchParams.get("look") || "vertical_wave";
      const cycleBeats = Number(url.searchParams.get("cycle_beats") || 4);
      const palette = url.searchParams.get("palette") || "orange";
      const intensity = url.searchParams.get("intensity") || "full";
      const bpm = Number(url.searchParams.get("bpm") || 120);
      return withCors(
        json(
          await previewFromWorkspace({
            sceneId,
            sceneName,
            files,
            look,
            cycleBeats: Number.isFinite(cycleBeats) ? cycleBeats : 4,
            palette,
            intensity,
            bpm: Number.isFinite(bpm) ? bpm : 120,
            assets: env.ASSETS,
          }),
        ),
        cors,
      );
    }

    if (path === "/admin/locations" && request.method === "GET") {
      const locations = await listOperatorLocations(env);
      return withCors(json({ locations }), cors);
    }

    const deviceMatch = /^\/admin\/devices\/([^/]+)$/.exec(path);
    if (deviceMatch && request.method === "GET") {
      const row = await getInstallation(env, decodeURIComponent(deviceMatch[1]!));
      if (!row) return withCors(errorJson(404, "not_found", "Device not found"), cors);
      const stub = deviceStub(env, row.id);
      const statusRes = await stub.fetch("https://device-do/status");
      const live = (await statusRes.json()) as {
        online?: boolean;
        device_connected?: boolean;
        [k: string]: unknown;
      };
      const cred = await env.DB.prepare(
        `SELECT rotation_pending FROM device_credentials WHERE installation_id = ?`,
      )
        .bind(row.id)
        .first<{ rotation_pending: number }>();
      return withCors(
        json({
          device: publicInstallation(row, {
            online: Boolean(live.online ?? live.device_connected),
            rotationPending: Boolean(cred?.rotation_pending),
          }),
          live,
        }),
        cors,
      );
    }

    if (path === "/admin/pair/approve" && request.method === "POST") {
      const body = (await request.json()) as {
        pairing_id: string;
        code: string;
        name: string;
        platform: Platform;
        installation_id?: string;
      };
      if (!body.pairing_id || !body.code || !body.name || !PLATFORMS.has(body.platform)) {
        return withCors(errorJson(400, "bad_request", "pairing_id, code, name, platform required"), cors);
      }
      const result = await approvePairing(env, {
        pairingId: body.pairing_id,
        code: body.code,
        name: body.name,
        platform: body.platform,
        actor,
        installationId: body.installation_id,
      });
      // No plaintext token — device decrypts envelope via /pair/complete.
      return withCors(
        json({
          approved: true,
          installation_id: result.installation_id,
          pairing: result.pairing,
        }),
        cors,
      );
    }

    const revokeMatch = /^\/admin\/devices\/([^/]+)\/revoke$/.exec(path);
    if (revokeMatch && request.method === "POST") {
      const id = decodeURIComponent(revokeMatch[1]!);
      await revokeInstallation(env, id, actor);
      return withCors(json({ ok: true, installation_id: id }), cors);
    }

    const rotateMatch = /^\/admin\/devices\/([^/]+)\/rotate$/.exec(path);
    if (rotateMatch && request.method === "POST") {
      const id = decodeURIComponent(rotateMatch[1]!);
      const body = (await request.json().catch(() => ({}))) as {
        public_key_jwk?: JsonWebKey;
      };
      // No plaintext token — optional public_key encrypts now; else device prepares.
      const result = await initiateTokenRotation(env, id, actor, {
        publicKeyJwk: body.public_key_jwk ?? null,
      });
      return withCors(
        json({
          installation_id: result.installation_id,
          rotation_pending: true,
          envelope_ready: result.envelope_ready,
          note: result.envelope_ready
            ? "Device must ACK via authenticated /device/rotation/ack"
            : "Device must POST /device/rotation/prepare with public_key_jwk, then ACK",
        }),
        cors,
      );
    }

    const commandsMatch = /^\/admin\/devices\/([^/]+)\/commands$/.exec(path);
    if (commandsMatch && request.method === "GET") {
      const id = decodeURIComponent(commandsMatch[1]!);
      const status = url.searchParams.get("status");
      const limit = Number(url.searchParams.get("limit") ?? 40);
      const commands = await listCommands(env, id, {
        limit: Number.isFinite(limit) ? limit : 40,
        status,
      });
      return withCors(json({ commands }), cors);
    }

    const controlSurfaceMatch = /^\/admin\/devices\/([^/]+)\/control-surface$/.exec(path);
    if (controlSurfaceMatch && request.method === "GET") {
      const id = decodeURIComponent(controlSurfaceMatch[1]!);
      const row = await getInstallation(env, id);
      if (!row) return withCors(errorJson(404, "not_found", "Device not found"), cors);
      const assignment = await getAssignment(env, id);
      const sceneId =
        row.current_scene_id ??
        (assignment?.scene_id != null ? String(assignment.scene_id) : null);
      let version: number | null =
        row.current_release_version != null ? Number(row.current_release_version) : null;
      if (version == null && assignment?.desired_version != null) {
        version = Number(assignment.desired_version);
      }
      if (version == null && sceneId) {
        const latest = await env.DB.prepare(
          `SELECT MAX(version) AS v FROM scene_releases WHERE scene_id = ?`,
        )
          .bind(sceneId)
          .first<{ v: number | null }>();
        version = latest?.v ?? null;
      }
      const sceneRow = sceneId
        ? await env.DB.prepare(`SELECT name FROM scenes WHERE scene_id = ?`)
            .bind(sceneId)
            .first<{ name: string | null }>()
        : null;
      const surface = await buildControlSurface(env, {
        sceneId,
        version,
        fallbackName: sceneRow?.name ?? null,
      });
      let online = false;
      try {
        const stub = deviceStub(env, id);
        const statusRes = await stub.fetch("https://device-do/status");
        const live = (await statusRes.json()) as { online?: boolean; device_connected?: boolean };
        online = Boolean(live.online ?? live.device_connected);
      } catch {
        online = false;
      }
      return withCors(
        json({
          installation_id: id,
          name: row.name,
          online,
          armed: Boolean(row.armed),
          blackout: Boolean(row.blackout),
          transport: row.transport,
          ...surface,
        }),
        cors,
      );
    }

    if (path === "/admin/audit" && request.method === "GET") {
      const limit = Number(url.searchParams.get("limit") ?? 50);
      const target = url.searchParams.get("target");
      const events = await listAudit(env, {
        limit: Number.isFinite(limit) ? limit : 50,
        target,
      });
      return withCors(json({ events }), cors);
    }

    const assignMatch = /^\/admin\/devices\/([^/]+)\/assign$/.exec(path);
    if (assignMatch && request.method === "POST") {
      const id = decodeURIComponent(assignMatch[1]!);
      const body = (await request.json()) as {
        scene_id: string;
        desired_version?: number | null;
        track_latest?: boolean;
      };
      const assigned = await assignScene(env, {
        installationId: id,
        sceneId: body.scene_id,
        desiredVersion: body.desired_version ?? null,
        trackLatest: body.track_latest !== false,
        actor,
      });
      const stub = deviceStub(env, id);
      await stub.fetch(
        new Request("https://device-do/command", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            type: "sync_scene_release",
            installation_id: id,
            actor,
            payload: {
              scene_id: body.scene_id,
              version: assigned.desired_version,
              track_latest: body.track_latest !== false,
            },
          }),
        }),
      );
      return withCors(json({ ok: true, desired_version: assigned.desired_version }), cors);
    }

    const workspaceMatch = /^\/admin\/devices\/([^/]+)\/scene-workspace$/.exec(path);
    if (workspaceMatch && request.method === "GET") {
      const id = decodeURIComponent(workspaceMatch[1]!);
      const workspace = await loadAssignedWorkspace(env, id);
      return withCors(json(workspace), cors);
    }
    if (workspaceMatch && request.method === "POST") {
      const id = decodeURIComponent(workspaceMatch[1]!);
      const body = (await request.json()) as {
        scene_id?: string;
        scene_name?: string;
        base_version?: number | null;
        files?: Array<{ path?: string; text?: string }>;
      };
      if (!body.scene_id || !Array.isArray(body.files) || body.files.length === 0) {
        return withCors(errorJson(400, "bad_request", "scene_id and files required"), cors);
      }
      const published = await publishAssignedWorkspace(env, {
        installationId: id,
        sceneId: body.scene_id,
        sceneName: body.scene_name || body.scene_id,
        baseVersion: body.base_version ?? null,
        files: body.files.map((file) => ({
          path: String(file.path ?? ""),
          text: String(file.text ?? ""),
        })),
        actor,
      });
      return withCors(json(published), cors);
    }

    if (path === "/admin/scenes" && request.method === "GET") {
      return withCors(json({ scenes: await listScenes(env) }), cors);
    }

    if (path === "/admin/storage" && request.method === "GET") {
      const { measureOrngStorage } = await import("../app/storage_budget");
      const report = await measureOrngStorage(env);
      return withCors(json({ storage: report, hardware_acceptance: "pending" }), cors);
    }

    const releasesMatch = /^\/admin\/scenes\/([^/]+)\/releases$/.exec(path);
    if (releasesMatch && request.method === "GET") {
      const sceneId = decodeURIComponent(releasesMatch[1]!);
      return withCors(json({ releases: await listReleases(env, sceneId) }), cors);
    }

    const downloadMatch = /^\/admin\/scenes\/([^/]+)\/releases\/(\d+)\/download$/.exec(path);
    if (downloadMatch && request.method === "GET") {
      const sceneId = decodeURIComponent(downloadMatch[1]!);
      const version = Number(downloadMatch[2]);
      const bundle = await getReleaseBundle(env, sceneId, version);
      if (!bundle) return withCors(errorJson(404, "not_found", "Release not found"), cors);
      return withCors(
        new Response(bundle.body.body, {
          headers: {
            "content-type": "application/json",
            "x-content-hash": bundle.manifest.content_sha256,
            "x-scene-id": sceneId,
            "x-version": String(version),
          },
        }),
        cors,
      );
    }

    const publishMatch = /^\/admin\/scenes\/([^/]+)\/publish$/.exec(path);
    if (publishMatch && request.method === "POST") {
      const sceneId = decodeURIComponent(publishMatch[1]!);
      const ct = request.headers.get("content-type") ?? "";
      let bundleBytes: Uint8Array;
      let sceneName = sceneId;
      let baseVersion: number | null = null;
      let minAppVersion: string | null = null;

      if (ct.includes("multipart/form-data")) {
        const form = await request.formData();
        const file = form.get("bundle");
        if (!file || typeof file === "string" || !("arrayBuffer" in file)) {
          return withCors(errorJson(400, "bad_request", "multipart field 'bundle' required"), cors);
        }
        bundleBytes = new Uint8Array(await (file as Blob).arrayBuffer());
        sceneName = String(form.get("name") ?? sceneId);
        const bv = form.get("base_version");
        baseVersion = bv === null || bv === "" ? null : Number(bv);
        minAppVersion = form.get("min_app_version") ? String(form.get("min_app_version")) : null;
      } else {
        const body = (await request.json()) as {
          name?: string;
          base_version?: number | null;
          min_app_version?: string | null;
          bundle_base64?: string;
          bundle?: string;
        };
        sceneName = body.name ?? sceneId;
        baseVersion = body.base_version ?? null;
        minAppVersion = body.min_app_version ?? null;
        const b64 = body.bundle_base64 ?? body.bundle;
        if (!b64) return withCors(errorJson(400, "bad_request", "bundle_base64 required"), cors);
        const binary = atob(b64);
        bundleBytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bundleBytes[i] = binary.charCodeAt(i);
      }

      const result = await publishRelease(env, {
        sceneId,
        sceneName,
        bundleBytes,
        baseVersion,
        publishedBy: actor,
        minAppVersion,
      });
      return withCors(
        json({
          release: result.release,
          idempotent: result.idempotent,
          assignments_updated: result.assignments_updated,
        }),
        cors,
      );
    }

    // --- Phase 3 APPLICATION releases (Access-only publish; device Bearer cannot) ---
    if (path === "/admin/app/releases" && request.method === "GET") {
      const platform = url.searchParams.get("platform") ?? undefined;
      const arch = url.searchParams.get("arch") ?? undefined;
      const channel = url.searchParams.get("channel") ?? undefined;
      const releases = await listAppReleases(env, { platform, arch, channel });
      return withCors(
        json({
          releases: releases.map((r) => ({
            ...r,
            manifest: JSON.parse(r.manifest_json),
          })),
        }),
        cors,
      );
    }

    if (path === "/admin/app/releases/publish" && request.method === "POST") {
      const parsed = await parseAppPublishBody(request);
      if (parsed instanceof Response) return withCors(parsed, cors);
      const result = await publishAppRelease(env, {
        ...parsed,
        publishedBy: actor,
        trust: { env, hostHeader: request.headers.get("host") },
      });
      return withCors(
        json({
          release: {
            ...result.release,
            manifest: JSON.parse(result.release.manifest_json),
          },
          idempotent: result.idempotent,
          assignments_updated: result.assignments_updated,
        }),
        cors,
      );
    }

    const appAssignMatch = /^\/admin\/installations\/([^/]+)\/app\/assign$/.exec(path);
    if (appAssignMatch && request.method === "POST") {
      const id = decodeURIComponent(appAssignMatch[1]!);
      const body = (await request.json()) as {
        desired_version?: string | null;
        desired_build_id?: string | null;
        channel?: string;
        track_latest?: boolean;
        /** Canonical: stage package when notified (device applies when safe). */
        auto_stage?: boolean;
        /** @deprecated Alias of auto_stage. */
        auto_update?: boolean;
      };
      const assigned = await assignAppRelease(env, {
        installationId: id,
        desiredVersion: body.desired_version ?? null,
        desiredBuildId: body.desired_build_id ?? null,
        channel: body.channel ?? "stable",
        trackLatest: body.track_latest !== false,
        autoStage: body.auto_stage === true || body.auto_update === true,
        actor,
      });
      return withCors(json({ ok: true, assignment: publicAppAssignment(assigned) }), cors);
    }

    if (path === "/admin/app/rollout" && request.method === "POST") {
      const body = (await request.json()) as {
        installation_ids: string[];
        desired_version: string;
        desired_build_id: string;
        channel?: string;
        track_latest?: boolean;
        auto_stage?: boolean;
        auto_update?: boolean;
      };
      if (!Array.isArray(body.installation_ids) || !body.desired_version || !body.desired_build_id) {
        return withCors(
          errorJson(400, "bad_request", "installation_ids, desired_version, desired_build_id required"),
          cors,
        );
      }
      const result = await bulkAppRollout(env, {
        installationIds: body.installation_ids,
        desiredVersion: body.desired_version,
        desiredBuildId: body.desired_build_id,
        channel: body.channel ?? "stable",
        trackLatest: body.track_latest === true,
        autoStage: body.auto_stage === true || body.auto_update === true,
        actor,
      });
      return withCors(json(result), cors);
    }

    const appRollbackMatch = /^\/admin\/installations\/([^/]+)\/app\/rollback$/.exec(path);
    if (appRollbackMatch && request.method === "POST") {
      const id = decodeURIComponent(appRollbackMatch[1]!);
      const body = (await request.json()) as {
        desired_version: string;
        desired_build_id: string;
        channel?: string;
      };
      if (!body.desired_version || !body.desired_build_id) {
        return withCors(
          errorJson(400, "bad_request", "desired_version and desired_build_id required"),
          cors,
        );
      }
      const assignment = await rollbackAppAssignment(env, {
        installationId: id,
        desiredVersion: body.desired_version,
        desiredBuildId: body.desired_build_id,
        channel: body.channel,
        actor,
      });
      return withCors(json({ ok: true, assignment: publicAppAssignment(assignment) }), cors);
    }

    if (path === "/admin/app/events" && request.method === "GET") {
      const installationId = url.searchParams.get("installation_id") ?? undefined;
      const limit = Number(url.searchParams.get("limit") ?? 50);
      const events = await listAppUpdateEvents(env, {
        installationId,
        limit: Number.isFinite(limit) ? limit : 50,
      });
      return withCors(json({ events }), cors);
    }

    const cmdMatch = /^\/admin\/devices\/([^/]+)\/command$/.exec(path);
    if (cmdMatch && request.method === "POST") {
      const id = decodeURIComponent(cmdMatch[1]!);
      const body = (await request.json()) as {
        type: string;
        payload?: Record<string, unknown>;
      };
      if (!isAllowedCommandType(body.type)) {
        return withCors(errorJson(400, "not_allowlisted", body.type), cors);
      }
      // Holds and keyboard belong to the admin WebSocket, which owns a per-tab
      // source. HTTP must not stamp every tab with the shared username.
      const socketOnly = new Set([
        "keyboard",
        "release_source",
        "strobe_press",
        "strobe_release",
        "sweep_press",
        "sweep_release",
        "vertical_sweep_press",
        "vertical_sweep_release",
        "calibration_begin",
        "calibration_set",
        "calibration_visible",
        "calibration_home",
        "calibration_end",
      ]);
      if (socketOnly.has(body.type)) {
        return withCors(
          errorJson(400, "use_control_socket", "Hold and keyboard commands use the admin WebSocket"),
          cors,
        );
      }
      const stub = deviceStub(env, id);
      const res = await stub.fetch(
        new Request("https://device-do/command", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            type: body.type,
            installation_id: id,
            actor,
            source: "cloud:http",
            payload: body.payload ?? {},
          }),
        }),
      );
      await writeAudit(env, actor, "remote.command", id, { type: body.type });
      const data = await res.json();
      return withCors(json(data, res.status), cors);
    }

    // Admin WSS proxy — unique source per tab.
    const wsMatch = /^\/admin\/ws\/device\/([^/]+)$/.exec(path);
    if (wsMatch && request.headers.get("Upgrade")?.toLowerCase() === "websocket") {
      const id = decodeURIComponent(wsMatch[1]!);
      const tabSessionId = uuid();
      const source = `cloud:web:${tabSessionId}`;
      await writeAudit(env, actor, "remote.ws_open", id, { source });
      return forwardAdminWs(env, id, request, source);
    }

    return withCors(errorJson(404, "not_found", "Unknown admin route"), cors);
  } catch (err) {
    if (
      err instanceof PairingError ||
      err instanceof ReleaseError ||
      err instanceof AppReleaseError
    ) {
      return withCors(errorJson(err.status, err.code, err.message), cors);
    }
    console.error("admin error", err);
    return withCors(errorJson(500, "internal", "Internal error"), cors);
  }
}

async function parseAppPublishBody(request: Request): Promise<
  | {
      version: string;
      build_id: string;
      platform: string;
      arch: string;
      channel: string;
      packageBytes: Uint8Array;
      signatureB64: string;
      keyId: string;
      notes: string | null;
      minUpdaterVersion: string | null;
      updaterProtocolVersion: number;
      manifest?: AppSigningManifest;
    }
  | Response
> {
  const ct = request.headers.get("content-type") ?? "";
  if (ct.includes("multipart/form-data")) {
    const form = await request.formData();
    const file = form.get("package") ?? form.get("bundle");
    if (!file || typeof file === "string" || !("arrayBuffer" in file)) {
      return errorJson(400, "bad_request", "multipart field 'package' required");
    }
    const packageBytes = new Uint8Array(await (file as Blob).arrayBuffer());
    const version = String(form.get("version") ?? "");
    const build_id = String(form.get("build_id") ?? "");
    const platform = String(form.get("platform") ?? "");
    const arch = String(form.get("arch") ?? "");
    const channel = String(form.get("channel") ?? "stable");
    const signatureB64 = String(form.get("signature_b64") ?? form.get("signature") ?? "");
    const keyId = String(form.get("key_id") ?? "");
    const notes = form.get("notes") ? String(form.get("notes")) : null;
    const minUpdaterVersion = form.get("min_updater_version")
      ? String(form.get("min_updater_version"))
      : null;
    const updaterProtocolVersion = form.get("updater_protocol_version")
      ? Number(form.get("updater_protocol_version"))
      : 1;
    let manifest: AppSigningManifest | undefined;
    const manifestRaw = form.get("manifest");
    if (manifestRaw && typeof manifestRaw === "string") {
      try {
        manifest = JSON.parse(manifestRaw) as AppSigningManifest;
      } catch {
        return errorJson(400, "bad_request", "manifest must be JSON");
      }
    }
    return {
      version,
      build_id,
      platform,
      arch,
      channel,
      packageBytes,
      signatureB64,
      keyId,
      notes,
      minUpdaterVersion,
      updaterProtocolVersion,
      manifest,
    };
  }

  const body = (await request.json()) as {
    version?: string;
    build_id?: string;
    platform?: string;
    arch?: string;
    channel?: string;
    package_base64?: string;
    signature_b64?: string;
    key_id?: string;
    notes?: string | null;
    min_updater_version?: string | null;
    updater_protocol_version?: number;
    manifest?: AppSigningManifest;
  };
  const b64 = body.package_base64;
  if (!b64) return errorJson(400, "bad_request", "package_base64 required");
  const binary = atob(b64);
  const packageBytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) packageBytes[i] = binary.charCodeAt(i);

  let manifest = body.manifest;
  if (!manifest && body.version && body.build_id && body.platform && body.arch) {
    const contentSha256 = await sha256Bytes(packageBytes);
    manifest = buildSigningManifest({
      version: body.version,
      build_id: body.build_id,
      platform: body.platform,
      arch: body.arch,
      channel: body.channel ?? "stable",
      content_sha256: contentSha256,
      package_size: packageBytes.byteLength,
      min_updater_version: body.min_updater_version,
      updater_protocol_version: body.updater_protocol_version,
      notes: body.notes,
    });
  }

  return {
    version: body.version ?? "",
    build_id: body.build_id ?? "",
    platform: body.platform ?? "",
    arch: body.arch ?? "",
    channel: body.channel ?? "stable",
    packageBytes,
    signatureB64: body.signature_b64 ?? "",
    keyId: body.key_id ?? "",
    notes: body.notes ?? null,
    minUpdaterVersion: body.min_updater_version ?? null,
    updaterProtocolVersion: body.updater_protocol_version ?? 1,
    manifest,
  };
}
function withCors(res: Response, cors: Headers): Response {
  const headers = new Headers(res.headers);
  cors.forEach((v, k) => headers.set(k, v));
  return new Response(res.body, { status: res.status, headers });
}

/** SPA shell + static assets — never admin API (`/admin/devices`, …). */
export function isAdminSpaPath(path: string): boolean {
  if (
    path.startsWith("/admin/") ||
    path.startsWith("/api/") ||
    path.startsWith("/pair") ||
    path.startsWith("/device") ||
    path.startsWith("/ws/")
  ) {
    return false;
  }
  if (path === "/admin" || path === "/admin/") return true;
  if (path.startsWith("/assets/")) return true;
  if (
    path === "/favicon.ico" ||
    path === "/logo.png" ||
    path === "/logo.svg" ||
    path === "/robots.txt"
  ) {
    return true;
  }
  return !path.includes(".");
}

/** Local-dev only when ASSETS binding is absent — not a production placeholder UI. */
const LOCAL_ADMIN_FALLBACK_HTML = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>ORNG LED Control</title>
  <style>
    body{font-family:ui-sans-serif,system-ui,sans-serif;background:#0c0c0c;color:#e8e8e8;margin:2rem;line-height:1.5}
    code{background:#1a1a1a;padding:.15rem .4rem;border-radius:4px}
  </style>
</head>
<body>
  <h1>ORNG LED Control</h1>
  <p>Admin API is available under <code>/admin/*</code> (login session required).</p>
  <p>Local note: configure Wrangler <code>assets.directory</code> (<code>admin-dist</code>) or build the cloud frontend into that path for a full UI.</p>
</body>
</html>`;
