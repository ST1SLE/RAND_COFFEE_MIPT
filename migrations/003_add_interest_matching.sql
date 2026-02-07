-- Миграция 003: Добавление режима "Мэтчинг по интересам"
--
-- Новый режим работает параллельно с существующим функционалом:
-- пользователи добровольно входят в режим поиска, раз в 1-2 дня matcher
-- подбирает пары по cosine similarity, затем пара договаривается о встрече
-- через бота и попадает в стандартный lifecycle.

-- Флаг участия в режиме поиска по интересам
ALTER TABLE users
ADD COLUMN IF NOT EXISTS is_searching_interest_match BOOLEAN DEFAULT FALSE;

-- Таблица мэтчей по интересам
-- Жизненный цикл: proposed → negotiating → accepted/declined/expired
CREATE TABLE IF NOT EXISTS interest_matches (
    match_id SERIAL PRIMARY KEY,
    user_1_id BIGINT NOT NULL REFERENCES users(user_id),
    user_2_id BIGINT NOT NULL REFERENCES users(user_id),
    similarity_score REAL NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'proposed',
        -- proposed: matcher создал пару, ожидает реакции
        -- negotiating: один из пользователей предложил встречу, ожидает ответа
        -- accepted: оба согласились, coffee_request создан
        -- declined: один из пользователей отклонил
        -- expired: таймаут (24ч без реакции / 12ч без ответа на предложение / >3 раундов)
    coffee_request_id INTEGER REFERENCES coffee_requests(request_id),
        -- заполняется при status=accepted, связь с созданной заявкой
    proposed_shop_id INTEGER REFERENCES coffee_shops(shop_id),
    proposed_meet_time TIMESTAMP WITH TIME ZONE,
    proposed_by BIGINT REFERENCES users(user_id),
        -- кто предложил текущие параметры встречи
    negotiation_round INTEGER DEFAULT 0,
        -- счетчик раундов переговоров (макс 3)
    university_id INTEGER NOT NULL REFERENCES universities(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_notification_sent BOOLEAN DEFAULT FALSE
        -- флаг для предотвращения повторной отправки уведомлений о мэтче
);

CREATE INDEX IF NOT EXISTS idx_interest_matches_status
ON interest_matches(status, university_id);

CREATE INDEX IF NOT EXISTS idx_interest_matches_users
ON interest_matches(user_1_id, user_2_id);
