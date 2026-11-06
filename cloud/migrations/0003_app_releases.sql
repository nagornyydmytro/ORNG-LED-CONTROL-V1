-- Phase 3: APPLICATION releases (separate from scene releases).
-- Do not edit 0001_init.sql / 0002_pairing_envelope.sql — additive only.

CREATE TABLE app_releases (
  id TEXT PRIMARY KEY NOT NULL,
  version TEXT NOT NULL,
  build_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  arch TEXT NOT NULL,
  channel TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  package_size INTEGER NOT NULL,
  manifest_json TEXT NOT NULL,
  signature_b64 TEXT NOT NULL,
  key_id TEXT NOT NULL,
  published_at TEXT NOT NULL,
  published_by TEXT,
  notes TEXT,
  min_updater_version TEXT,
  updater_protocol_version INTEGER NOT NULL DEFAULT 1,
  r2_key TEXT NOT NULL,
  UNIQUE (version, build_id, platform, arch, channel)
);

CREATE INDEX idx_app_releases_channel ON app_releases(platform, arch, channel, published_at);
CREATE INDEX idx_app_releases_hash ON app_releases(content_sha256);

CREATE TABLE app_assignments (
  installation_id TEXT PRIMARY KEY NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
  desired_version TEXT,
  desired_build_id TEXT,
  channel TEXT NOT NULL DEFAULT 'stable',
  track_latest INTEGER NOT NULL DEFAULT 1,
  auto_update INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE app_update_events (
  id TEXT PRIMARY KEY NOT NULL,
  installation_id TEXT NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX idx_app_update_events_install ON app_update_events(installation_id, created_at);
CREATE INDEX idx_app_update_events_created ON app_update_events(created_at);
