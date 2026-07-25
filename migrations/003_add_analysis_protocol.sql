-- Additive migration for existing SQLite classification histories.
-- Application startup performs the same guarded change after inspecting PRAGMA.
ALTER TABLE ia_classifications ADD COLUMN protocol TEXT;
