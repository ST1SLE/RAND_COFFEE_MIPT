-- Синхронизация БД при переходе с main на recsys.
-- Идемпотентно (IF NOT EXISTS / IF EXISTS).

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT DEFAULT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS embedding vector(384);

ALTER TABLE coffee_requests ADD COLUMN IF NOT EXISTS is_match_notification_sent BOOLEAN DEFAULT FALSE;

UPDATE coffee_requests SET is_match_notification_sent = TRUE
WHERE status = 'matched' AND is_match_notification_sent = FALSE;

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_searching_interest_match BOOLEAN DEFAULT FALSE;

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

CREATE INDEX IF NOT EXISTS idx_interest_matches_status ON interest_matches(status, university_id);
CREATE INDEX IF NOT EXISTS idx_interest_matches_users ON interest_matches(user_1_id, user_2_id);

ALTER TABLE interest_matches ADD COLUMN IF NOT EXISTS is_proposal_reminder_sent BOOLEAN DEFAULT FALSE;

ALTER TABLE users ADD COLUMN IF NOT EXISTS gender VARCHAR(10) DEFAULT NULL;

ALTER TABLE coffee_shops ADD COLUMN IF NOT EXISTS promo_label VARCHAR(50) DEFAULT NULL;
ALTER TABLE coffee_shops ADD COLUMN IF NOT EXISTS partner_chat_id BIGINT[] DEFAULT NULL;
ALTER TABLE coffee_shops ADD COLUMN IF NOT EXISTS discount_amount INTEGER DEFAULT NULL;

-- (name) → (name, university_id)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'coffee_shops_name_key'
          AND conrelid = 'coffee_shops'::regclass
    ) THEN
        ALTER TABLE coffee_shops DROP CONSTRAINT coffee_shops_name_key;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'coffee_shops_name_university_id_key'
          AND conrelid = 'coffee_shops'::regclass
    ) THEN
        ALTER TABLE coffee_shops ADD CONSTRAINT coffee_shops_name_university_id_key UNIQUE (name, university_id);
    END IF;
END $$;

ALTER TABLE coffee_requests ADD COLUMN IF NOT EXISTS verification_code VARCHAR(10) DEFAULT NULL;
