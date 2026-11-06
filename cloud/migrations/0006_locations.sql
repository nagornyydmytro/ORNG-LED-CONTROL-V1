-- A location outlives any one Raspberry or laptop.
-- Pairing must bind to an existing location; it must not insert one.

CREATE TABLE IF NOT EXISTS locations (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  scene_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);

ALTER TABLE installations ADD COLUMN location_id TEXT;

INSERT INTO locations (id, name, scene_id, created_at)
SELECT 'loc-orng-bar', 'ORNG BAR', 'orng-bar', '2026-10-27T00:00:00.000Z'
WHERE NOT EXISTS (SELECT 1 FROM locations WHERE id = 'loc-orng-bar');

UPDATE installations
SET location_id = 'loc-orng-bar'
WHERE id = '7e4457ad-c47e-4aaf-beba-87f83a1c92d0'
  AND (location_id IS NULL OR location_id = '');
