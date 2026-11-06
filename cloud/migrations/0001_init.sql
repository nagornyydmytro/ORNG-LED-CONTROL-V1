-- ORNG LED Control — Phase 2 control-plane schema

CREATE TABLE installations (
  id TEXT PRIMARY KEY NOT NULL,
  name TEXT NOT NULL,
  platform TEXT NOT NULL CHECK (platform IN ('raspberry', 'desktop_windows', 'desktop_macos', 'development')),
  app_version TEXT,
  build_id TEXT,
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  current_scene_id TEXT,
  current_release_version INTEGER,
  current_content_hash TEXT,
  armed INTEGER NOT NULL DEFAULT 0,
  blackout INTEGER NOT NULL DEFAULT 1,
  transport TEXT,
  udp_active INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  uptime_s REAL,
  last_seen_at TEXT,
  created_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE TABLE device_credentials (
  installation_id TEXT PRIMARY KEY NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL,
  rotated_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE pairing_sessions (
  id TEXT PRIMARY KEY NOT NULL,
  code_hash TEXT NOT NULL,
  client_challenge TEXT NOT NULL,
  installation_id_pending TEXT,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  created_at TEXT NOT NULL,
  ip_hint TEXT
);

CREATE INDEX idx_pairing_sessions_expires ON pairing_sessions(expires_at);
CREATE INDEX idx_pairing_sessions_code_hash ON pairing_sessions(code_hash);

CREATE TABLE scenes (
  scene_id TEXT PRIMARY KEY NOT NULL,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE scene_releases (
  scene_id TEXT NOT NULL REFERENCES scenes(scene_id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  r2_key TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  published_by TEXT,
  published_at TEXT NOT NULL,
  PRIMARY KEY (scene_id, version),
  UNIQUE (scene_id, content_hash)
);

CREATE INDEX idx_scene_releases_scene ON scene_releases(scene_id);

CREATE TABLE scene_assignments (
  installation_id TEXT PRIMARY KEY NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
  scene_id TEXT NOT NULL REFERENCES scenes(scene_id),
  desired_version INTEGER,
  track_latest INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);

CREATE TABLE command_log (
  command_id TEXT PRIMARY KEY NOT NULL,
  installation_id TEXT NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  acked_at TEXT
);

CREATE INDEX idx_command_log_installation ON command_log(installation_id, created_at);

CREATE TABLE audit_events (
  id TEXT PRIMARY KEY NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX idx_audit_events_created ON audit_events(created_at);
CREATE INDEX idx_installations_last_seen ON installations(last_seen_at);
