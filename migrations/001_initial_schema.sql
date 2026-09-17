-- =============================================================================
-- Voice Translator — Initial schema (idempotent)
-- =============================================================================

CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    email        TEXT UNIQUE,
    display_name TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_language_preferences (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    speak      VARCHAR(8) NOT NULL,
    hear       VARCHAR(8) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_user_lang_prefs_user_id ON user_language_preferences(user_id);

CREATE TABLE IF NOT EXISTS calls (
    id          TEXT PRIMARY KEY,
    caller_id   TEXT NOT NULL REFERENCES users(id),
    receiver_id TEXT NOT NULL REFERENCES users(id),
    status      VARCHAR(16) NOT NULL DEFAULT 'pending',
    started_at  TIMESTAMPTZ,
    ended_at    TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_calls_caller_id   ON calls(caller_id);
CREATE INDEX IF NOT EXISTS idx_calls_receiver_id ON calls(receiver_id);
CREATE INDEX IF NOT EXISTS idx_calls_status      ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_created_at  ON calls(created_at DESC);

CREATE TABLE IF NOT EXISTS call_participants (
    id               TEXT PRIMARY KEY,
    call_id          TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    user_id          TEXT NOT NULL REFERENCES users(id),
    source_language  VARCHAR(8) NOT NULL,
    target_language  VARCHAR(8) NOT NULL,
    joined_at        TIMESTAMPTZ,
    left_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_call_participants_call_id ON call_participants(call_id);
CREATE INDEX IF NOT EXISTS idx_call_participants_user_id ON call_participants(user_id);

CREATE TABLE IF NOT EXISTS usage (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES users(id),
    call_id             TEXT REFERENCES calls(id),
    audio_seconds       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    translation_seconds DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_usage_user_id    ON usage(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_call_id    ON usage(call_id);
CREATE INDEX IF NOT EXISTS idx_usage_created_at ON usage(created_at DESC);

CREATE TABLE IF NOT EXISTS call_quality_metrics (
    id            TEXT PRIMARY KEY,
    call_id       TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    participant_id TEXT NOT NULL,
    metric_name   VARCHAR(64) NOT NULL,
    metric_value  DOUBLE PRECISION NOT NULL,
    payload       TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cqm_call_id       ON call_quality_metrics(call_id);
CREATE INDEX IF NOT EXISTS idx_cqm_participant   ON call_quality_metrics(participant_id);
CREATE INDEX IF NOT EXISTS idx_cqm_metric_name   ON call_quality_metrics(metric_name);
CREATE INDEX IF NOT EXISTS idx_cqm_created_at    ON call_quality_metrics(created_at DESC);
