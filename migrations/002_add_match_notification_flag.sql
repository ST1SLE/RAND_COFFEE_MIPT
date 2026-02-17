-- Добавляем флаг для отслеживания отправки уведомлений о автоматическом мэтчинге
-- Нужно, чтобы не отправлять уведомление дважды, если заявка была замэтчена через ML matcher

ALTER TABLE coffee_requests
ADD COLUMN IF NOT EXISTS is_match_notification_sent BOOLEAN DEFAULT FALSE;

-- Проставляем TRUE для всех уже существующих matched заявок,
-- чтобы не отправлять им уведомления задним числом
UPDATE coffee_requests
SET is_match_notification_sent = TRUE
WHERE status = 'matched';
