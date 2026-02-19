-- 'M', 'F', 'skip', NULL (ещё не спрашивали)
ALTER TABLE users ADD COLUMN IF NOT EXISTS gender VARCHAR(10) DEFAULT NULL;
