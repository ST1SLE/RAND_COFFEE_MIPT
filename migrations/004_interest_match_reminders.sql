ALTER TABLE interest_matches
ADD COLUMN IF NOT EXISTS is_proposal_reminder_sent BOOLEAN DEFAULT FALSE;
