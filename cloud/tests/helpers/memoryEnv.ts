/**
 * Minimal in-memory D1 + R2 + fake DOs for unit/integration tests (no Cloudflare account).
 */

import { hashToken } from "../../src/auth/device";
import { defaultScopesForPlatform } from "../../src/auth/scopes";
import type { Platform } from "../../src/db/installations";
import type { Env } from "../../src/env";
import {
  PAIRING_MAX_ACTIVE_SESSIONS,
  PAIRING_RATE_MAX,
  PAIRING_RATE_WINDOW_MS,
} from "../../src/durable/PairingRateLimitDO";
import {
  ADMIN_LOGIN_FAIL_WINDOW_MS,
  ADMIN_LOGIN_LOCKOUT_MS,
  ADMIN_LOGIN_MAX_FAILURES,
} from "../../src/durable/AdminLoginGuardDO";

type Row = Record<string, unknown>;

class MemoryD1Prepared {
  constructor(
    private db: MemoryD1,
    private sql: string,
    private binds: unknown[] = [],
  ) {}

  bind(...args: unknown[]): MemoryD1Prepared {
    return new MemoryD1Prepared(this.db, this.sql, args);
  }

  async first<T = Row>(): Promise<T | null> {
    const all = await this.all<T>();
    return all.results[0] ?? null;
  }

  async run(): Promise<D1Result> {
    const changes = this.db.exec(this.sql, this.binds);
    return {
      success: true,
      meta: {
        duration: 0,
        changes,
        last_row_id: 0,
        changed_db: changes > 0,
        size_after: 0,
        rows_read: 0,
        rows_written: changes,
      },
    } as D1Result;
  }

  async all<T = Row>(): Promise<D1Result<T>> {
    const results = this.db.query(this.sql, this.binds) as T[];
    return {
      success: true,
      results,
      meta: {
        duration: 0,
        changes: 0,
        last_row_id: 0,
        changed_db: false,
        size_after: 0,
        rows_read: results.length,
        rows_written: 0,
      },
    } as D1Result<T>;
  }
}

export class MemoryD1 {
  tables: Record<string, Row[]> = {
    installations: [],
    locations: [],
    device_credentials: [],
    pairing_sessions: [],
    scenes: [],
    scene_releases: [],
    scene_assignments: [],
    command_log: [],
    audit_events: [],
    app_releases: [],
    app_assignments: [],
    app_update_events: [],
  };

  prepare(sql: string): MemoryD1Prepared {
    return new MemoryD1Prepared(this, sql);
  }

  async batch(statements: MemoryD1Prepared[]): Promise<D1Result[]> {
    const out: D1Result[] = [];
    for (const s of statements) out.push(await s.run());
    return out;
  }

  query(sql: string, binds: unknown[]): Row[] {
    const s = sql.replace(/\s+/g, " ").trim();

    if (/SELECT \* FROM installations WHERE id = \?/i.test(s)) {
      return this.tables.installations.filter((r) => r.id === binds[0]);
    }
    if (/SELECT \* FROM installations ORDER BY/i.test(s)) {
      return [...this.tables.installations].sort((a, b) =>
        String(b.created_at).localeCompare(String(a.created_at)),
      );
    }
    if (/FROM locations WHERE id = \?/i.test(s)) {
      return this.tables.locations.filter((r) => r.id === binds[0]);
    }
    if (/FROM locations ORDER BY/i.test(s)) {
      return [...this.tables.locations].sort((a, b) =>
        String(a.name ?? "").localeCompare(String(b.name ?? "")),
      );
    }
    if (/SELECT id, location_id FROM installations WHERE location_id IS NOT NULL/i.test(s)) {
      return this.tables.installations
        .filter((r) => r.location_id)
        .map((r) => ({ id: r.id, location_id: r.location_id }));
    }
    if (/SELECT current_release_version FROM installations WHERE id = \?/i.test(s)) {
      return this.tables.installations
        .filter((r) => r.id === binds[0])
        .map((r) => ({ current_release_version: r.current_release_version ?? null }));
    }
    if (/SELECT c\.installation_id/i.test(s)) {
      const hash = binds[0];
      const hash2 = binds[1] ?? hash;
      const cred = this.tables.device_credentials.find(
        (c) =>
          c.token_hash === hash ||
          (Number(c.rotation_pending) === 1 && c.pending_token_hash === hash2),
      );
      if (!cred) return [];
      const inst = this.tables.installations.find((i) => i.id === cred.installation_id);
      return [
        {
          installation_id: cred.installation_id,
          scopes_json: cred.scopes_json ?? "[]",
          rotation_pending: cred.rotation_pending ?? 0,
          pending_token_hash: cred.pending_token_hash ?? null,
          revoked_at: inst?.revoked_at ?? null,
          platform: inst?.platform ?? null,
        },
      ];
    }
    if (/SELECT rotation_pending FROM device_credentials WHERE installation_id = \?/i.test(s)) {
      return this.tables.device_credentials
        .filter((r) => r.installation_id === binds[0])
        .map((r) => ({ rotation_pending: r.rotation_pending ?? 0 }));
    }
    if (/SELECT rotation_pending, rotation_envelope FROM device_credentials/i.test(s)) {
      return this.tables.device_credentials
        .filter((r) => r.installation_id === binds[0])
        .map((r) => ({
          rotation_pending: r.rotation_pending ?? 0,
          rotation_envelope: r.rotation_envelope ?? null,
        }));
    }
    if (/SELECT token_hash, pending_token_hash, rotation_pending/i.test(s)) {
      return this.tables.device_credentials
        .filter((r) => r.installation_id === binds[0])
        .map((r) => ({
          token_hash: r.token_hash,
          pending_token_hash: r.pending_token_hash ?? null,
          rotation_pending: r.rotation_pending ?? 0,
        }));
    }
    if (/SELECT \* FROM pairing_sessions WHERE id = \?/i.test(s)) {
      return this.tables.pairing_sessions.filter((r) => r.id === binds[0]);
    }
    if (/SELECT id, revoked_at FROM installations WHERE id = \?/i.test(s)) {
      return this.tables.installations
        .filter((r) => r.id === binds[0])
        .map((r) => ({ id: r.id, revoked_at: r.revoked_at }));
    }
    if (/SELECT \* FROM scenes/i.test(s)) {
      return [...this.tables.scenes];
    }
    if (/SELECT scene_id FROM scenes WHERE scene_id = \?/i.test(s)) {
      return this.tables.scenes
        .filter((r) => r.scene_id === binds[0])
        .map((r) => ({ scene_id: r.scene_id }));
    }
    if (/SELECT version, content_hash, manifest_json, size_bytes FROM scene_releases/i.test(s)) {
      return this.tables.scene_releases.filter(
        (r) => r.scene_id === binds[0] && r.content_hash === binds[1],
      );
    }
    if (/SELECT MAX\(version\) AS v FROM scene_releases/i.test(s)) {
      const rows = this.tables.scene_releases.filter((r) => r.scene_id === binds[0]);
      const v = rows.reduce((m, r) => Math.max(m, Number(r.version)), 0);
      return [{ v: rows.length ? v : null }];
    }
    if (/SELECT scene_id, version, content_hash/i.test(s)) {
      return this.tables.scene_releases
        .filter((r) => r.scene_id === binds[0])
        .sort((a, b) => Number(b.version) - Number(a.version));
    }
    if (/SELECT r2_key, manifest_json FROM scene_releases/i.test(s)) {
      return this.tables.scene_releases.filter(
        (r) => r.scene_id === binds[0] && Number(r.version) === Number(binds[1]),
      );
    }
    if (/SELECT manifest_json FROM scene_releases WHERE scene_id/i.test(s)) {
      return this.tables.scene_releases.filter(
        (r) => r.scene_id === binds[0] && r.content_hash === binds[1],
      );
    }
    if (/SELECT \* FROM scene_assignments WHERE installation_id = \?/i.test(s)) {
      return this.tables.scene_assignments.filter((r) => r.installation_id === binds[0]);
    }
    if (/SELECT installation_id FROM scene_assignments WHERE scene_id = \? AND track_latest = 1/i.test(s)) {
      return this.tables.scene_assignments
        .filter((r) => r.scene_id === binds[0] && Number(r.track_latest) === 1)
        .map((r) => ({ installation_id: r.installation_id }));
    }
    if (
      /SELECT installation_id, desired_version, track_latest\s+FROM scene_assignments WHERE scene_id = \? AND track_latest = 1/i.test(
        s,
      )
    ) {
      return this.tables.scene_assignments
        .filter((r) => r.scene_id === binds[0] && Number(r.track_latest) === 1)
        .map((r) => ({
          installation_id: r.installation_id,
          desired_version: r.desired_version,
          track_latest: r.track_latest,
        }));
    }
    if (/SELECT \* FROM command_log WHERE command_id = \?/i.test(s)) {
      return this.tables.command_log.filter((r) => r.command_id === binds[0]);
    }
    if (/FROM command_log\s+WHERE installation_id = \? AND status = \?/i.test(s)) {
      const limit = Number(binds[2] ?? 40);
      return this.tables.command_log
        .filter((r) => r.installation_id === binds[0] && r.status === binds[1])
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
        .slice(0, limit);
    }
    if (/FROM command_log\s+WHERE installation_id = \?/i.test(s)) {
      const limit = Number(binds[1] ?? 40);
      return this.tables.command_log
        .filter((r) => r.installation_id === binds[0])
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
        .slice(0, limit);
    }
    if (/FROM audit_events WHERE target = \?/i.test(s)) {
      const limit = Number(binds[1] ?? 50);
      return this.tables.audit_events
        .filter((r) => r.target === binds[0])
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
        .slice(0, limit);
    }
    if (/FROM audit_events ORDER BY created_at DESC/i.test(s)) {
      const limit = Number(binds[0] ?? 50);
      return [...this.tables.audit_events]
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
        .slice(0, limit);
    }
    // --- app releases ---
    if (/SELECT \* FROM app_releases WHERE 1=1/i.test(s)) {
      let rows = [...this.tables.app_releases];
      let bi = 0;
      if (/AND platform = \?/i.test(s)) {
        rows = rows.filter((r) => r.platform === binds[bi++]);
      }
      if (/AND arch = \?/i.test(s)) {
        rows = rows.filter((r) => r.arch === binds[bi++]);
      }
      if (/AND channel = \?/i.test(s)) {
        rows = rows.filter((r) => r.channel === binds[bi++]);
      }
      return rows.sort((a, b) => String(b.published_at).localeCompare(String(a.published_at)));
    }
    if (
      /SELECT \* FROM app_releases\s+WHERE platform = \? AND arch = \? AND version = \? AND build_id = \? AND channel = \?/i.test(
        s,
      )
    ) {
      return this.tables.app_releases.filter(
        (r) =>
          r.platform === binds[0] &&
          r.arch === binds[1] &&
          r.version === binds[2] &&
          r.build_id === binds[3] &&
          r.channel === binds[4],
      );
    }
    if (
      /SELECT \* FROM app_releases\s+WHERE platform = \? AND arch = \? AND version = \? AND build_id = \?/i.test(
        s,
      )
    ) {
      return this.tables.app_releases
        .filter(
          (r) =>
            r.platform === binds[0] &&
            r.arch === binds[1] &&
            r.version === binds[2] &&
            r.build_id === binds[3],
        )
        .sort((a, b) => String(b.published_at).localeCompare(String(a.published_at)))
        .slice(0, 1);
    }
    if (
      /SELECT \* FROM app_releases\s+WHERE version = \? AND build_id = \? AND platform = \? AND arch = \? AND channel = \?/i.test(
        s,
      )
    ) {
      return this.tables.app_releases.filter(
        (r) =>
          r.version === binds[0] &&
          r.build_id === binds[1] &&
          r.platform === binds[2] &&
          r.arch === binds[3] &&
          r.channel === binds[4],
      );
    }
    if (
      /SELECT version, build_id FROM app_releases\s+WHERE platform = \? AND arch = \? AND channel = \?/i.test(
        s,
      )
    ) {
      return this.tables.app_releases
        .filter(
          (r) => r.platform === binds[0] && r.arch === binds[1] && r.channel === binds[2],
        )
        .sort((a, b) => String(b.published_at).localeCompare(String(a.published_at)))
        .slice(0, 1)
        .map((r) => ({ version: r.version, build_id: r.build_id }));
    }
    if (/SELECT \* FROM app_assignments WHERE installation_id = \?/i.test(s)) {
      return this.tables.app_assignments.filter((r) => r.installation_id === binds[0]);
    }
    if (
      /SELECT a\.installation_id, a\.channel, a\.track_latest, i\.platform/i.test(s) &&
      /FROM app_assignments a/i.test(s) &&
      !/desired_version/i.test(s)
    ) {
      return this.tables.app_assignments
        .filter((a) => Number(a.track_latest) === 1 && a.channel === binds[0])
        .map((a) => {
          const i = this.tables.installations.find((x) => x.id === a.installation_id);
          if (!i || i.revoked_at) return null;
          return {
            installation_id: a.installation_id,
            channel: a.channel,
            track_latest: a.track_latest,
            platform: i.platform,
            arch: i.arch ?? null,
            capabilities_json: i.capabilities_json,
          };
        })
        .filter(Boolean) as Row[];
    }
    if (
      /SELECT a\.installation_id, a\.desired_version, a\.desired_build_id, a\.channel/i.test(s) &&
      /a\.auto_update/i.test(s) &&
      /FROM app_assignments a/i.test(s)
    ) {
      return this.tables.app_assignments
        .filter((a) => Number(a.track_latest) === 1 && a.channel === binds[0])
        .map((a) => {
          const i = this.tables.installations.find((x) => x.id === a.installation_id);
          if (!i || i.revoked_at) return null;
          return {
            installation_id: a.installation_id,
            desired_version: a.desired_version,
            desired_build_id: a.desired_build_id,
            channel: a.channel,
            auto_update: a.auto_update,
            platform: i.platform,
            arch: i.arch ?? null,
            capabilities_json: i.capabilities_json,
          };
        })
        .filter(Boolean) as Row[];
    }
    if (
      /SELECT id, platform(?:, arch)?, capabilities_json, revoked_at FROM installations WHERE id = \?/i.test(
        s,
      )
    ) {
      return this.tables.installations
        .filter((r) => r.id === binds[0])
        .map((r) => ({
          id: r.id,
          platform: r.platform,
          arch: r.arch ?? null,
          capabilities_json: r.capabilities_json,
          revoked_at: r.revoked_at,
        }));
    }
    if (
      /SELECT platform(?:, arch)?, capabilities_json, app_version, build_id, revoked_at/i.test(s) &&
      /FROM installations WHERE id = \?/i.test(s)
    ) {
      return this.tables.installations
        .filter((r) => r.id === binds[0])
        .map((r) => ({
          platform: r.platform,
          arch: r.arch ?? null,
          capabilities_json: r.capabilities_json,
          app_version: r.app_version,
          build_id: r.build_id,
          revoked_at: r.revoked_at,
        }));
    }
    if (/SELECT platform(?:, arch)?, capabilities_json FROM installations WHERE id = \?/i.test(s)) {
      return this.tables.installations
        .filter((r) => r.id === binds[0])
        .map((r) => ({
          platform: r.platform,
          arch: r.arch ?? null,
          capabilities_json: r.capabilities_json,
        }));
    }
    if (/SELECT capabilities_json FROM installations WHERE id = \?/i.test(s)) {
      return this.tables.installations
        .filter((r) => r.id === binds[0])
        .map((r) => ({ capabilities_json: r.capabilities_json }));
    }
    if (
      /SELECT id, detail_json FROM app_update_events\s+WHERE installation_id = \? AND event_type = \?/i.test(
        s,
      )
    ) {
      const limit = Number(binds[2] ?? 20);
      return this.tables.app_update_events
        .filter((r) => r.installation_id === binds[0] && r.event_type === binds[1])
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
        .slice(0, limit)
        .map((r) => ({ id: r.id, detail_json: r.detail_json }));
    }
    if (
      /SELECT id FROM app_update_events\s+WHERE installation_id = \? AND event_type = \?/i.test(s)
    ) {
      return this.tables.app_update_events
        .filter((r) => r.installation_id === binds[0] && r.event_type === binds[1])
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
        .slice(0, 1)
        .map((r) => ({ id: r.id }));
    }
    if (
      /FROM app_update_events WHERE installation_id = \?/i.test(s)
    ) {
      const limit = Number(binds[1] ?? 50);
      return this.tables.app_update_events
        .filter((r) => r.installation_id === binds[0])
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
        .slice(0, limit);
    }
    if (/FROM app_update_events ORDER BY created_at DESC/i.test(s)) {
      const limit = Number(binds[0] ?? 50);
      return [...this.tables.app_update_events]
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
        .slice(0, limit);
    }
    return [];
  }

  exec(sql: string, binds: unknown[]): number {
    const s = sql.replace(/\s+/g, " ").trim();

    if (/INSERT INTO pairing_sessions/i.test(s)) {
      this.tables.pairing_sessions.push({
        id: binds[0],
        code_hash: binds[1],
        client_challenge: binds[2],
        installation_id_pending: binds[3],
        expires_at: binds[4],
        used_at: null,
        created_at: binds[5],
        ip_hint: binds[6],
        public_key_jwk: binds[7] ?? null,
        credential_envelope: null,
        envelope_delivered_at: null,
        envelope_consumed_at: null,
        approved_at: null,
      });
      return 1;
    }
    if (/UPDATE pairing_sessions SET\s+used_at/i.test(s)) {
      const row = this.tables.pairing_sessions.find((r) => r.id === binds[4]);
      if (row) {
        row.used_at = binds[0];
        row.approved_at = binds[1];
        row.installation_id_pending = binds[2];
        row.credential_envelope = binds[3];
      }
      return row ? 1 : 0;
    }
    if (/UPDATE pairing_sessions SET envelope_delivered_at/i.test(s)) {
      const row = this.tables.pairing_sessions.find((r) => r.id === binds[1]);
      if (row) row.envelope_delivered_at = binds[0];
      return row ? 1 : 0;
    }
    if (/UPDATE pairing_sessions SET\s+envelope_consumed_at/i.test(s)) {
      const row = this.tables.pairing_sessions.find((r) => r.id === binds[1]);
      if (row) {
        row.envelope_consumed_at = binds[0];
        row.credential_envelope = null;
      }
      return row ? 1 : 0;
    }
    if (/UPDATE pairing_sessions SET code_hash/i.test(s)) {
      const row = this.tables.pairing_sessions.find((r) => r.id === binds[1]);
      if (row) row.code_hash = binds[0];
      return row ? 1 : 0;
    }
    if (/INSERT INTO installations/i.test(s)) {
      const existing = this.tables.installations.find((r) => r.id === binds[0]);
      if (existing) {
        existing.name = binds[1];
        existing.platform = binds[2];
        existing.revoked_at = null;
      } else {
        this.tables.installations.push({
          id: binds[0],
          name: binds[1],
          platform: binds[2],
          capabilities_json: "{}",
          armed: 0,
          blackout: 1,
          created_at: binds[3],
          revoked_at: null,
          app_version: null,
          build_id: null,
          current_scene_id: null,
          current_release_version: null,
          current_content_hash: null,
          last_synced_hash: null,
          transport: null,
          udp_active: 0,
          last_error: null,
          uptime_s: null,
          last_seen_at: null,
        });
      }
      return 1;
    }
    if (/INSERT INTO device_credentials/i.test(s)) {
      const existing = this.tables.device_credentials.find((r) => r.installation_id === binds[0]);
      if (existing) {
        existing.token_hash = binds[1];
        existing.rotated_at = binds[2];
        if (binds[3] !== undefined) existing.scopes_json = binds[3];
        existing.rotation_pending = 0;
        existing.pending_token_hash = null;
        existing.rotation_envelope = null;
        existing.rotation_started_at = null;
      } else {
        this.tables.device_credentials.push({
          installation_id: binds[0],
          token_hash: binds[1],
          created_at: binds[2],
          rotated_at: null,
          scopes_json: binds[3] ?? "[]",
          rotation_pending: 0,
          pending_token_hash: null,
          rotation_envelope: null,
          rotation_started_at: null,
        });
      }
      return 1;
    }
    if (/UPDATE device_credentials SET\s+rotation_pending = 1/i.test(s)) {
      const row = this.tables.device_credentials.find((r) => r.installation_id === binds[3]);
      if (row) {
        row.rotation_pending = 1;
        row.pending_token_hash = binds[0];
        row.rotation_envelope = binds[1];
        row.rotation_started_at = binds[2];
      }
      return row ? 1 : 0;
    }
    if (/UPDATE device_credentials SET\s+token_hash = \?/i.test(s)) {
      const row = this.tables.device_credentials.find((r) => r.installation_id === binds[2]);
      if (row) {
        row.token_hash = binds[0];
        row.rotated_at = binds[1];
        row.rotation_pending = 0;
        row.pending_token_hash = null;
        row.rotation_envelope = null;
        row.rotation_started_at = null;
      }
      return row ? 1 : 0;
    }
    if (/UPDATE installations SET revoked_at/i.test(s)) {
      const row = this.tables.installations.find((r) => r.id === binds[1]);
      if (row) row.revoked_at = binds[0];
      return row ? 1 : 0;
    }
    if (/DELETE FROM device_credentials/i.test(s)) {
      const before = this.tables.device_credentials.length;
      this.tables.device_credentials = this.tables.device_credentials.filter(
        (r) => r.installation_id !== binds[0],
      );
      return before - this.tables.device_credentials.length;
    }
    if (/INSERT INTO scenes/i.test(s)) {
      const existing = this.tables.scenes.find((r) => r.scene_id === binds[0]);
      if (existing) {
        existing.name = binds[1];
        existing.updated_at = binds[3];
      } else {
        this.tables.scenes.push({
          scene_id: binds[0],
          name: binds[1],
          created_at: binds[2],
          updated_at: binds[3],
        });
      }
      return 1;
    }
    if (/INSERT INTO scene_releases/i.test(s)) {
      const dupHash = this.tables.scene_releases.find(
        (r) => r.scene_id === binds[0] && r.content_hash === binds[2],
      );
      if (dupHash) throw new Error("UNIQUE constraint failed: scene_id,content_hash");
      const dupVer = this.tables.scene_releases.find(
        (r) => r.scene_id === binds[0] && Number(r.version) === Number(binds[1]),
      );
      if (dupVer) throw new Error("UNIQUE constraint failed: scene_id,version");
      this.tables.scene_releases.push({
        scene_id: binds[0],
        version: binds[1],
        content_hash: binds[2],
        r2_key: binds[3],
        manifest_json: binds[4],
        size_bytes: binds[5],
        published_by: binds[6],
        published_at: binds[7],
      });
      return 1;
    }
    if (/UPDATE scene_assignments\s+SET desired_version/i.test(s)) {
      let n = 0;
      for (const row of this.tables.scene_assignments) {
        const nextVersion = Number(binds[0]);
        const current = row.desired_version == null ? null : Number(row.desired_version);
        if (
          row.scene_id === binds[2] &&
          Number(row.track_latest) === 1 &&
          (current == null || current < nextVersion)
        ) {
          row.desired_version = binds[0];
          row.updated_at = binds[1];
          n += 1;
        }
      }
      return n;
    }
    if (/INSERT INTO scene_assignments/i.test(s)) {
      const existing = this.tables.scene_assignments.find((r) => r.installation_id === binds[0]);
      if (existing) {
        existing.scene_id = binds[1];
        existing.desired_version = binds[2];
        existing.track_latest = binds[3];
        existing.updated_at = binds[4];
      } else {
        this.tables.scene_assignments.push({
          installation_id: binds[0],
          scene_id: binds[1],
          desired_version: binds[2],
          track_latest: binds[3],
          updated_at: binds[4],
        });
      }
      return 1;
    }
    if (/INSERT INTO command_log/i.test(s)) {
      if (this.tables.command_log.some((r) => r.command_id === binds[0])) return 0;
      this.tables.command_log.push({
        command_id: binds[0],
        installation_id: binds[1],
        type: binds[2],
        payload_json: binds[3],
        status: binds[4],
        error_code: binds[5],
        error_message: binds[6],
        created_at: binds[7],
        acked_at: null,
      });
      return 1;
    }
    if (/UPDATE command_log SET status/i.test(s)) {
      // Variants:
      // WHERE command_id = ?
      // WHERE command_id = ? AND installation_id = ?
      // WHERE command_id = ? AND installation_id = ? AND type = ?
      const commandId = binds[4];
      const installationId = binds.length >= 6 ? binds[5] : undefined;
      const type = binds.length >= 7 ? binds[6] : undefined;
      const row = this.tables.command_log.find((r) => {
        if (r.command_id !== commandId) return false;
        if (installationId !== undefined && r.installation_id !== installationId) return false;
        if (type !== undefined && r.type !== type) return false;
        return true;
      });
      if (row) {
        row.status = binds[0];
        row.error_code = binds[1];
        row.error_message = binds[2];
        row.acked_at = binds[3];
      }
      return row ? 1 : 0;
    }
    if (/INSERT INTO audit_events/i.test(s)) {
      this.tables.audit_events.push({
        id: binds[0],
        actor: binds[1],
        action: binds[2],
        target: binds[3],
        detail_json: binds[4],
        created_at: binds[5],
      });
      return 1;
    }
    if (/UPDATE installations SET\s+last_seen_at/i.test(s)) {
      const id = binds[binds.length - 1];
      const row = this.tables.installations.find((r) => r.id === id);
      if (row) {
        row.last_seen_at = binds[0];
        // touchHeartbeat binds: [now, uptime, armed, blackout, transport, udp,
        //   scene, release_ver, content_hash, last_synced_hash, last_error,
        //   app_version, build_id, arch, install_mode, updater_protocol_version,
        //   capabilities, id]
        if (binds.length >= 8 && binds[6] != null) row.current_scene_id = binds[6];
        if (binds.length >= 9 && binds[7] != null) row.current_release_version = binds[7];
        if (binds.length >= 10 && binds[8] != null) row.current_content_hash = binds[8];
        if (binds.length >= 11 && binds[9] != null) row.last_synced_hash = binds[9];
        if (binds.length >= 13 && binds[11] != null) row.app_version = binds[11];
        if (binds.length >= 14 && binds[12] != null) row.build_id = binds[12];
        if (binds.length >= 15 && binds[13] != null) row.arch = binds[13];
        if (binds.length >= 16 && binds[14] != null) row.install_mode = binds[14];
        if (binds.length >= 17 && binds[15] != null) {
          row.updater_protocol_version = binds[15];
        }
        if (binds.length >= 18 && binds[16] != null) {
          row.capabilities_json = binds[16];
        }
      }
      return row ? 1 : 0;
    }
    if (/UPDATE installations SET app_version = \?, build_id = COALESCE/i.test(s)) {
      const row = this.tables.installations.find((r) => r.id === binds[2]);
      if (row) {
        row.app_version = binds[0];
        if (binds[1] != null) row.build_id = binds[1];
      }
      return row ? 1 : 0;
    }
    if (/INSERT INTO app_releases/i.test(s)) {
      const dup = this.tables.app_releases.find(
        (r) =>
          r.version === binds[1] &&
          r.build_id === binds[2] &&
          r.platform === binds[3] &&
          r.arch === binds[4] &&
          r.channel === binds[5],
      );
      if (dup) throw new Error("UNIQUE constraint failed: app_releases identity");
      this.tables.app_releases.push({
        id: binds[0],
        version: binds[1],
        build_id: binds[2],
        platform: binds[3],
        arch: binds[4],
        channel: binds[5],
        content_sha256: binds[6],
        package_size: binds[7],
        manifest_json: binds[8],
        signature_b64: binds[9],
        key_id: binds[10],
        published_at: binds[11],
        published_by: binds[12],
        notes: binds[13],
        min_updater_version: binds[14],
        updater_protocol_version: binds[15],
        r2_key: binds[16],
      });
      return 1;
    }
    if (
      /UPDATE app_assignments\s+SET desired_version = \?, desired_build_id = \?, updated_at = \?\s+WHERE installation_id = \?/i.test(
        s,
      )
    ) {
      const row = this.tables.app_assignments.find((r) => r.installation_id === binds[3]);
      if (row) {
        row.desired_version = binds[0];
        row.desired_build_id = binds[1];
        row.updated_at = binds[2];
      }
      return row ? 1 : 0;
    }
    if (/INSERT INTO app_assignments/i.test(s)) {
      const existing = this.tables.app_assignments.find((r) => r.installation_id === binds[0]);
      if (existing) {
        existing.desired_version = binds[1];
        existing.desired_build_id = binds[2];
        existing.channel = binds[3];
        existing.track_latest = binds[4];
        existing.auto_update = binds[5];
        existing.updated_at = binds[6];
      } else {
        this.tables.app_assignments.push({
          installation_id: binds[0],
          desired_version: binds[1],
          desired_build_id: binds[2],
          channel: binds[3],
          track_latest: binds[4],
          auto_update: binds[5],
          updated_at: binds[6],
        });
      }
      return 1;
    }
    if (/INSERT INTO app_update_events/i.test(s)) {
      this.tables.app_update_events.push({
        id: binds[0],
        installation_id: binds[1],
        event_type: binds[2],
        detail_json: binds[3],
        created_at: binds[4],
      });
      return 1;
    }
    return 0;
  }
}

class MemoryR2 {
  store = new Map<string, Uint8Array>();

  async put(
    key: string,
    value: ArrayBuffer | ArrayBufferView | string | Blob | ReadableStream,
    _opts?: unknown,
  ) {
    let bytes: Uint8Array;
    if (typeof value === "string") bytes = new TextEncoder().encode(value);
    else if (value instanceof Uint8Array) bytes = value;
    else if (ArrayBuffer.isView(value)) bytes = new Uint8Array(value.buffer);
    else if (value instanceof ArrayBuffer) bytes = new Uint8Array(value);
    else throw new Error("unsupported r2 put");
    this.store.set(key, bytes);
    return { key } as R2Object;
  }

  async get(key: string): Promise<R2ObjectBody | null> {
    const data = this.store.get(key);
    if (!data) return null;
    return {
      body: data,
      arrayBuffer: async () => data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength),
      text: async () => new TextDecoder().decode(data),
      key,
    } as unknown as R2ObjectBody;
  }

  async delete(key: string): Promise<void> {
    this.store.delete(key);
  }

  async list(options?: { cursor?: string; limit?: number; prefix?: string }): Promise<{
    objects: Array<{ key: string; size: number }>;
    truncated: boolean;
    cursor?: string;
  }> {
    const limit = options?.limit ?? 1000;
    let keys = [...this.store.keys()].sort();
    if (options?.prefix) {
      keys = keys.filter((k) => k.startsWith(options.prefix!));
    }
    let start = 0;
    if (options?.cursor) {
      const idx = keys.indexOf(options.cursor);
      start = idx >= 0 ? idx + 1 : 0;
    }
    const slice = keys.slice(start, start + limit);
    const truncated = start + limit < keys.length;
    return {
      objects: slice.map((key) => ({ key, size: this.store.get(key)?.byteLength ?? 0 })),
      truncated,
      cursor: truncated ? slice[slice.length - 1] : undefined,
    };
  }
}

/** In-memory AdminLoginGuardDO (IP keyed). */
class FakeAdminLoginGuardNamespace {
  private guards = new Map<string, { failures: number[]; lockedUntil: number }>();

  idFromName(name: string): DurableObjectId {
    return { name, toString: () => name } as DurableObjectId;
  }

  get(id: DurableObjectId): DurableObjectStub {
    const name = String(id);
    return {
      fetch: async (input: RequestInfo | URL, init?: RequestInit) => {
        const req = input instanceof Request ? input : new Request(String(input), init);
        const url = new URL(req.url);
        const guard = this.guards.get(name) ?? { failures: [], lockedUntil: 0 };
        this.guards.set(name, guard);
        const now = Date.now();
        guard.failures = guard.failures.filter((t) => now - t < ADMIN_LOGIN_FAIL_WINDOW_MS);
        if (guard.lockedUntil && guard.lockedUntil <= now) guard.lockedUntil = 0;

        if (url.pathname === "/check" && req.method === "POST") {
          if (guard.lockedUntil > now) {
            return Response.json(
              { allowed: false, reason: "locked", retry_after_ms: guard.lockedUntil - now },
              { status: 429 },
            );
          }
          return Response.json({ allowed: true, failures: guard.failures.length });
        }
        if (url.pathname === "/failure" && req.method === "POST") {
          guard.failures.push(now);
          if (guard.failures.length >= ADMIN_LOGIN_MAX_FAILURES) {
            guard.lockedUntil = now + ADMIN_LOGIN_LOCKOUT_MS;
            guard.failures = [];
          }
          return Response.json({
            allowed: guard.lockedUntil <= now,
            locked: guard.lockedUntil > now,
            failures: guard.failures.length,
            retry_after_ms: guard.lockedUntil > now ? guard.lockedUntil - now : 0,
          });
        }
        if (url.pathname === "/success" && req.method === "POST") {
          guard.failures = [];
          guard.lockedUntil = 0;
          return Response.json({ ok: true });
        }
        return new Response("not found", { status: 404 });
      },
    } as unknown as DurableObjectStub;
  }
}

/** In-memory PairingRateLimitDO behavior (IP keyed). */
class FakePairingRateLimitNamespace {
  private buckets = new Map<
    string,
    { attempts: number[]; activeSessions: Array<{ id: string; expiresAt: number }> }
  >();

  idFromName(name: string): DurableObjectId {
    return { name, toString: () => name } as DurableObjectId;
  }

  get(id: DurableObjectId): DurableObjectStub {
    const name = String(id);
    return {
      fetch: async (input: RequestInfo | URL, init?: RequestInit) => {
        const req = input instanceof Request ? input : new Request(String(input), init);
        const url = new URL(req.url);
        const bucket = this.buckets.get(name) ?? { attempts: [], activeSessions: [] };
        this.buckets.set(name, bucket);
        const now = Date.now();
        bucket.attempts = bucket.attempts.filter((t) => now - t < PAIRING_RATE_WINDOW_MS);
        bucket.activeSessions = bucket.activeSessions.filter((s) => s.expiresAt > now);

        if (url.pathname === "/check" && req.method === "POST") {
          const body = (await req.json()) as {
            pairing_id?: string;
            expires_at?: string;
            register?: boolean;
          };
          if (bucket.attempts.length >= PAIRING_RATE_MAX) {
            return Response.json({ allowed: false, reason: "rate_limited" }, { status: 429 });
          }
          if (bucket.activeSessions.length >= PAIRING_MAX_ACTIVE_SESSIONS) {
            return Response.json({ allowed: false, reason: "too_many_sessions" }, { status: 429 });
          }
          bucket.attempts.push(now);
          if (body.register && body.pairing_id && body.expires_at) {
            bucket.activeSessions.push({
              id: body.pairing_id,
              expiresAt: Date.parse(body.expires_at),
            });
          }
          return Response.json({ allowed: true });
        }
        if (url.pathname === "/release" && req.method === "POST") {
          const body = (await req.json()) as { pairing_id?: string };
          bucket.activeSessions = bucket.activeSessions.filter((s) => s.id !== body.pairing_id);
          return Response.json({ ok: true });
        }
        return new Response("not found", { status: 404 });
      },
    } as unknown as DurableObjectStub;
  }
}

/** Fake DO namespace that records commands and online state. */
class FakeDeviceDONamespace {
  stubs = new Map<string, { commands: unknown[]; connected: boolean }>();

  idFromName(name: string): DurableObjectId {
    return { name, toString: () => name } as DurableObjectId;
  }

  get(id: DurableObjectId): DurableObjectStub {
    const name = String(id);
    if (!this.stubs.has(name)) this.stubs.set(name, { commands: [], connected: false });
    const state = this.stubs.get(name)!;
    return {
      fetch: async (input: RequestInfo | URL, init?: RequestInit) => {
        const req = input instanceof Request ? input : new Request(String(input), init);
        const url = new URL(req.url);
        if (url.pathname === "/command" && req.method === "POST") {
          const body = await req.json();
          if (!state.connected) {
            return Response.json(
              { ok: false, command_id: "fake", status: "failed", error: "device_offline" },
              { status: 409 },
            );
          }
          state.commands.push(body);
          return Response.json({ ok: true, command_id: "fake", status: "accepted" });
        }
        if (url.pathname === "/update-result" && req.method === "POST") {
          return Response.json({ ok: true, cleared: true });
        }
        if (url.pathname === "/status") {
          return Response.json({
            online: state.connected,
            device_connected: state.connected,
            pending_commands: state.commands.length,
          });
        }
        if (url.pathname.startsWith("/connect/")) {
          return new Response("upgrade not simulated in unit tests", { status: 426 });
        }
        return new Response("not found", { status: 404 });
      },
    } as unknown as DurableObjectStub;
  }

  setConnected(installationId: string, connected: boolean): void {
    if (!this.stubs.has(installationId)) {
      this.stubs.set(installationId, { commands: [], connected });
    } else {
      this.stubs.get(installationId)!.connected = connected;
    }
  }
}

export function createMemoryEnv(
  overrides: Partial<Env> = {},
): Env & {
  __db: MemoryD1;
  __r2: MemoryR2;
  __appR2: MemoryR2;
  __do: FakeDeviceDONamespace;
  __rl: FakePairingRateLimitNamespace;
  __loginGuard: FakeAdminLoginGuardNamespace;
} {
  const db = new MemoryD1();
  const r2 = new MemoryR2();
  const appR2 = new MemoryR2();
  const dos = new FakeDeviceDONamespace();
  const rl = new FakePairingRateLimitNamespace();
  const loginGuard = new FakeAdminLoginGuardNamespace();
  return {
    DB: db as unknown as D1Database,
    SCENE_RELEASES: r2 as unknown as R2Bucket,
    APP_RELEASES: appR2 as unknown as R2Bucket,
    DEVICE_DO: dos as unknown as DurableObjectNamespace,
    PAIRING_RATE_LIMIT_DO: rl as unknown as DurableObjectNamespace,
    ADMIN_LOGIN_GUARD_DO: loginGuard as unknown as DurableObjectNamespace,
    ADMIN_ORIGIN: "http://127.0.0.1:8787",
    ADMIN_USERNAME: "y3k_notfound",
    ADMIN_PASSWORD_HASH: undefined,
    ADMIN_SESSION_SECRET: "test-session-secret-not-for-prod",
    ADMIN_HOST: "light.orng.wtf",
    DEVICE_HOST: "device.light.orng.wtf",
    LOCAL_ADMIN_HOST: "localhost:8787",
    LOCAL_DEVICE_HOST: "localhost:8788",
    PROTOCOL_VERSION: "1",
    DEV_BYPASS_ACCESS: "1",
    DEV_ALLOW_QUERY_TOKEN: undefined,
    DEV_ALLOW_TEST_APP_KEYS: "1",
    APP_TRUSTED_KEYS: undefined,
    __db: db,
    __r2: r2,
    __appR2: appR2,
    __do: dos,
    __rl: rl,
    __loginGuard: loginGuard,
    ...overrides,
  };
}

export async function seedCredential(
  env: ReturnType<typeof createMemoryEnv>,
  installationId: string,
  token: string,
  opts?: { platform?: Platform; scopes?: string[]; capabilities?: Record<string, unknown> },
): Promise<void> {
  const now = new Date().toISOString();
  const platform = opts?.platform ?? "development";
  const scopes = opts?.scopes ?? defaultScopesForPlatform(platform);
  const capabilities = opts?.capabilities ?? { arch: "arm64" };
  const archFromCaps =
    typeof capabilities.arch === "string" ? (capabilities.arch as string) : null;
  env.__db.tables.installations.push({
    id: installationId,
    name: "test",
    platform,
    capabilities_json: JSON.stringify(capabilities),
    arch: archFromCaps,
    install_mode: null,
    updater_protocol_version: null,
    armed: 0,
    blackout: 1,
    created_at: now,
    revoked_at: null,
    app_version: null,
    build_id: null,
    current_scene_id: null,
    current_release_version: null,
    current_content_hash: null,
    last_synced_hash: null,
    transport: null,
    udp_active: 0,
    last_error: null,
    uptime_s: null,
    last_seen_at: null,
  });
  env.__db.tables.device_credentials.push({
    installation_id: installationId,
    token_hash: await hashToken(token),
    created_at: now,
    rotated_at: null,
    scopes_json: JSON.stringify(scopes),
    rotation_pending: 0,
    pending_token_hash: null,
    rotation_envelope: null,
    rotation_started_at: null,
  });
}
