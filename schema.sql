CREATE TYPE request_status_enum as ENUM (
    'pending',
    'matched',
    'cancelled',
    'expired'
);

CREATE TABLE users (
    user_id BIGINT PRIMARY KEY,
    username VARCHAR(255),
    first_name VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE coffee_shops (
    shop_id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    working_hours JSONB,
    is_active BOOLEAN DEFAULT true
);

CREATE TABLE coffee_requests (
    request_id SERIAL PRIMARY KEY,
    creator_user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    partner_user_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    shop_id INTEGER NOT NULL REFERENCES coffee_shops(shop_id) ON DELETE RESTRICT,
    meet_time TIMESTAMP WITH TIME ZONE NOT NULL,
    status request_status_enum NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_reminder_sent BOOLEAN NOT NULL DEFAULT false,
    is_failure_notification_sent BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX ON coffee_requests (creator_user_id);
CREATE INDEX ON coffee_requests (partner_user_id);
CREATE INDEX ON coffee_requests (status, meet_time);