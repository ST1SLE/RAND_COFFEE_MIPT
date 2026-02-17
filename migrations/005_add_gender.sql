-- Миграция 005: Добавление поля пола пользователя
-- Значения: 'M' (мужской), 'F' (женский), 'skip' (не хочу указывать), NULL (ещё не спрашивали)

ALTER TABLE users ADD COLUMN IF NOT EXISTS gender VARCHAR(10) DEFAULT NULL;
