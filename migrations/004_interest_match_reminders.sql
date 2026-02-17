-- Миграция 004: Добавление трекинга напоминаний для interest_matches
--
-- Новая колонка позволяет отслеживать, было ли отправлено напоминание
-- партнеру, который не ответил на предложение встречи (>6 часов).

ALTER TABLE interest_matches
ADD COLUMN IF NOT EXISTS is_proposal_reminder_sent BOOLEAN DEFAULT FALSE;
