-- Explicit executor. A missing Raspberry must not promote a laptop.
ALTER TABLE locations ADD COLUMN executor_installation_id TEXT;

UPDATE locations
SET executor_installation_id = '7e4457ad-c47e-4aaf-beba-87f83a1c92d0'
WHERE id = 'loc-orng-bar'
  AND (executor_installation_id IS NULL OR executor_installation_id = '');
