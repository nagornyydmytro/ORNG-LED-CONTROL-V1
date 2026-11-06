-- Pairing credential envelopes, scopes, and safe token rotation.
-- Do not edit 0001_init.sql — additive only.

ALTER TABLE pairing_sessions ADD COLUMN public_key_jwk TEXT;
ALTER TABLE pairing_sessions ADD COLUMN credential_envelope BLOB;
ALTER TABLE pairing_sessions ADD COLUMN envelope_delivered_at TEXT;
ALTER TABLE pairing_sessions ADD COLUMN envelope_consumed_at TEXT;
ALTER TABLE pairing_sessions ADD COLUMN approved_at TEXT;

ALTER TABLE device_credentials ADD COLUMN scopes_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE device_credentials ADD COLUMN rotation_pending INTEGER NOT NULL DEFAULT 0;
ALTER TABLE device_credentials ADD COLUMN pending_token_hash TEXT;
ALTER TABLE device_credentials ADD COLUMN rotation_envelope BLOB;
ALTER TABLE device_credentials ADD COLUMN rotation_started_at TEXT;
