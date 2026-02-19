ALTER TABLE users
ADD COLUMN IF NOT EXISTS is_searching_interest_match BOOLEAN DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS interest_matches (
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

CREATE INDEX IF NOT EXISTS idx_interest_matches_status
ON interest_matches(status, university_id);

CREATE INDEX IF NOT EXISTS idx_interest_matches_users
ON interest_matches(user_1_id, user_2_id);
