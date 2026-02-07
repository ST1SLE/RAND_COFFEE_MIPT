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

CREATE TABLE universities (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(50) UNIQUE NOT NULL, -- 'mipt', 'hse', 'msu' и т.д.
    name VARCHAR(255) NOT NULL,
    timezone VARCHAR(50) DEFAULT 'Europe/Moscow',
    is_active BOOLEAN DEFAULT TRUE
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
    year_as_student INTEGER,
    no_show_count INTEGER DEFAULT 0,
    coffee_streak INTEGER DEFAULT 0,
    university_id INTEGER REFERENCES universities(id),
    is_searching_interest_match BOOLEAN DEFAULT FALSE
);

-- Таблица кофеен
CREATE TABLE coffee_shops (
    shop_id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    working_hours JSONB,
    is_active BOOLEAN DEFAULT true,
    university_id INTEGER REFERENCES universities(id),
    promo_label VARCHAR(50) DEFAULT NULL,
    partner_chat_id BIGINT[] DEFAULT NULL,
    UNIQUE (name, university_id),
    discount_amount INTEGER DEFAULT NULL
);


INSERT INTO universities (slug, name) VALUES 
    ('mipt', 'МФТИ'),
    ('hse', 'ВШЭ'),
    ('misis', 'МИСиС'),
    ('bmtsu', 'МГТУ им. Баумана'),
    ('cu', 'Центральный Университет')
ON CONFLICT (slug) DO NOTHING;

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
    is_icebreaker_sent BOOLEAN NOT NULL DEFAULT false,
    feedback_text TEXT,
    is_confirmed_by_creator BOOLEAN DEFAULT FALSE,
    is_confirmed_by_partner BOOLEAN DEFAULT FALSE,
    is_confirmation_sent BOOLEAN DEFAULT FALSE,
    is_match_notification_sent BOOLEAN DEFAULT FALSE,  -- Флаг для уведомлений о ML-мэтчинге
    university_id INTEGER REFERENCES universities(id),
    verification_code VARCHAR(10) DEFAULT NULL
);

CREATE TABLE cancellation_logs (
    log_id SERIAL PRIMARY KEY,
    request_id INTEGER NOT NULL REFERENCES coffee_requests(request_id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    event_type cancellation_event_enum NOT NULL,
    event_time TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);


-- Таблица мэтчей по интересам (режим "Мэтчинг по интересам")
CREATE TABLE interest_matches (
    match_id SERIAL PRIMARY KEY,
    user_1_id BIGINT NOT NULL REFERENCES users(user_id),
    user_2_id BIGINT NOT NULL REFERENCES users(user_id),
    similarity_score REAL NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'proposed',
    coffee_request_id INTEGER REFERENCES coffee_requests(request_id),
    proposed_shop_id INTEGER REFERENCES coffee_shops(shop_id),
    proposed_meet_time TIMESTAMP WITH TIME ZONE,
    proposed_by BIGINT REFERENCES users(user_id),
    negotiation_round INTEGER DEFAULT 0,
    university_id INTEGER NOT NULL REFERENCES universities(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_notification_sent BOOLEAN DEFAULT FALSE
);

-- Индексы для ускорения выборок
CREATE INDEX ON coffee_requests (creator_user_id);
CREATE INDEX ON coffee_requests (partner_user_id);
CREATE INDEX ON coffee_requests (status, meet_time);
CREATE INDEX ON coffee_requests (university_id);

CREATE INDEX idx_universities_slug ON universities(slug);

CREATE INDEX ON cancellation_logs (request_id);
CREATE INDEX ON cancellation_logs (user_id);

CREATE INDEX ON users (university_id);

CREATE INDEX ON coffee_shops (university_id);

CREATE INDEX idx_interest_matches_status ON interest_matches(status, university_id);
CREATE INDEX idx_interest_matches_users ON interest_matches(user_1_id, user_2_id);