-- Discord snowflakes are stored as BIGINT: Postgres has no UNSIGNED type, but every
-- snowflake fits comfortably within signed 64-bit range, so plain BIGINT is fine.

CREATE TABLE IF NOT EXISTS moderations (
    id          SERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    action      VARCHAR(10) NOT NULL,
    created     BIGINT NOT NULL,
    value       INT NOT NULL DEFAULT 0,
    expire_time BIGINT NULL DEFAULT NULL,
    reason      TEXT,
    comment     TEXT NULL DEFAULT NULL,
    moderator   BIGINT NULL DEFAULT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS moderations_action_active_expire_time
    ON moderations (action, active, expire_time);
CREATE INDEX IF NOT EXISTS moderations_guild_user_action_active
    ON moderations (guild_id, user_id, action, active);
CREATE INDEX IF NOT EXISTS moderations_moderator_guild_action
    ON moderations (moderator, guild_id, action);
CREATE INDEX IF NOT EXISTS moderations_guild_created
    ON moderations (guild_id, created);

CREATE TABLE IF NOT EXISTS confirmations (
    id      SERIAL PRIMARY KEY,
    data    TEXT NOT NULL,
    expires BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS confirmations_expires ON confirmations (expires);

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id BIGINT PRIMARY KEY,
    data     JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS bad_words (
    id                   SERIAL PRIMARY KEY,
    guild_id             BIGINT NOT NULL,
    trigger_type         VARCHAR(10) NOT NULL,
    trigger_content      TEXT NOT NULL,
    trigger_flags        TEXT NULL DEFAULT NULL,
    punishment_action    VARCHAR(10) NOT NULL DEFAULT 'none',
    punishment_duration  BIGINT NULL DEFAULT NULL,
    punishment_count     INT NULL DEFAULT NULL,
    response             TEXT NOT NULL DEFAULT 'disabled',
    dm                   TEXT NULL DEFAULT NULL,
    is_global            BOOLEAN NOT NULL DEFAULT FALSE,
    channel_ids          BIGINT[] NOT NULL DEFAULT '{}',
    priority             INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS bad_words_guild_id ON bad_words (guild_id);
CREATE INDEX IF NOT EXISTS bad_words_channel_ids ON bad_words USING GIN (channel_ids);

CREATE TABLE IF NOT EXISTS auto_responses (
    id               SERIAL PRIMARY KEY,
    guild_id         BIGINT NOT NULL,
    trigger_type     VARCHAR(10) NOT NULL,
    trigger_content  TEXT NOT NULL,
    trigger_flags    TEXT NULL DEFAULT NULL,
    response         TEXT NOT NULL,
    is_global        BOOLEAN NOT NULL DEFAULT FALSE,
    channel_ids      BIGINT[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS auto_responses_guild_id ON auto_responses (guild_id);
CREATE INDEX IF NOT EXISTS auto_responses_channel_ids ON auto_responses USING GIN (channel_ids);

CREATE TABLE IF NOT EXISTS channel_settings (
    channel_id BIGINT PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    data       JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS channel_settings_guild_id ON channel_settings (guild_id);
