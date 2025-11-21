-- Пользовательский тип для статусов заявок
CREATE TYPE request_status_enum AS ENUM (
    'pending',
    'matched',
    'cancelled',
    'expired'
);

-- Пользовательский тип для результатов встреч
CREATE TYPE meeting_outcome_enum AS ENUM (
    'attended',
    'partner_no_show',
    'creator_no_show',
    'both_no_show'
);

CREATE TYPE cancellation_event_enum AS ENUM (
    'partner_unmatch',       
    'creator_cancel_matched',
    'creator_cancel_pending',
    'admin_ban_cancel' 
);

-- Таблица пользователей
CREATE TABLE users (
    user_id BIGINT PRIMARY KEY,
    username VARCHAR(255),
    first_name VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    phystech_school VARCHAR(50),
    year_as_student INTEGER
);

-- Таблица кофеен
CREATE TABLE coffee_shops (
    shop_id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    working_hours JSONB,
    is_active BOOLEAN DEFAULT true
);

-- Таблица заявок на кофе
CREATE TABLE coffee_requests (
    request_id SERIAL PRIMARY KEY,
    creator_user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    partner_user_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    shop_id INTEGER NOT NULL REFERENCES coffee_shops(shop_id) ON DELETE RESTRICT,
    meet_time TIMESTAMP WITH TIME ZONE NOT NULL,
    status request_status_enum NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_reminder_sent BOOLEAN NOT NULL DEFAULT false,
    is_failure_notification_sent BOOLEAN NOT NULL DEFAULT false,
    -- Колонки для сбора фидбека
    is_feedback_requested BOOLEAN NOT NULL DEFAULT false,
    meeting_outcome meeting_outcome_enum,
    is_icebreaker_sent BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE cancellation_logs (
    log_id SERIAL PRIMARY KEY,
    request_id INTEGER NOT NULL REFERENCES coffee_requests(request_id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    event_type cancellation_event_enum NOT NULL,
    event_time TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);


-- Индексы для ускорения выборок
CREATE INDEX ON coffee_requests (creator_user_id);
CREATE INDEX ON coffee_requests (partner_user_id);
CREATE INDEX ON coffee_requests (status, meet_time);
CREATE INDEX ON cancellation_logs (request_id);
CREATE INDEX ON cancellation_logs (user_id);