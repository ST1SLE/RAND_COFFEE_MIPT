ALTER TABLE coffee_requests
ADD COLUMN IF NOT EXISTS is_match_notification_sent BOOLEAN DEFAULT FALSE;

-- не отправлять уведомления задним числом
UPDATE coffee_requests
SET is_match_notification_sent = TRUE
WHERE status = 'matched';
