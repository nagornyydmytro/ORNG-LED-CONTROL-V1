-- Phase 3 repair: persist installation arch / updater fields from heartbeat.
-- Additive only — do not edit prior migrations.
-- app_assignments.auto_update remains the DB column; API exposes it as auto_stage
-- (stage-only policy; device applies when safe via apply_app_update / update_result).

ALTER TABLE installations ADD COLUMN arch TEXT;
ALTER TABLE installations ADD COLUMN install_mode TEXT;
ALTER TABLE installations ADD COLUMN updater_protocol_version INTEGER;
