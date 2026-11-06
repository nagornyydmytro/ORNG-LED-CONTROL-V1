-- Persist last_synced_hash from device heartbeat for admin divergence display.
-- Additive only — do not edit prior migrations.

ALTER TABLE installations ADD COLUMN last_synced_hash TEXT;
