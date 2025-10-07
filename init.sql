-- Create schema
CREATE SCHEMA IF NOT EXISTS social_manager;

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Create ENUM types
CREATE TYPE action_type AS ENUM ('VIEW', 'CREATE', 'UPDATE', 'DELETE', 'ANALYZE', 'MODERATE', 'EXPORT', 'CONFIGURE');
CREATE TYPE user_role_type AS ENUM ('VIEWER', 'AI_BOT', 'MANAGER', 'ANALYST', 'MODERATOR', 'ADMIN', 'SUPERUSER');
CREATE TYPE platform_type AS ENUM ('social', 'messenger');
CREATE TYPE source_type AS ENUM (
    -- Общие
    'user', 'group', 'channel', 'chat', 'page',
    -- VK специфичные
    'public', 'event', 'market', 'album',
    -- Telegram специфичные
    'supergroup', 'bot', 'broadcast'
    );
CREATE TYPE notification_type AS ENUM (
    'report_ready', 'mood_change', 'trend_alert', 'topic_resumed',
    'api_error', 'connection_error', 'source_inactive', 'rate_limit_warning',
    'bot_comment', 'bot_skipped', 'system_backup', 'system_update',
    'subscribed_activity', 'keyword_mention'
    );
CREATE TYPE analysis_period_type AS ENUM ('daily', 'weekly', 'monthly', 'custom');

-- Users table
CREATE TABLE IF NOT EXISTS social_manager.users
(
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(50) UNIQUE  NOT NULL,
    email           VARCHAR(100) UNIQUE NOT NULL,
    hashed_password VARCHAR(255),
    is_active       BOOLEAN                  DEFAULT TRUE,
    is_superuser    BOOLEAN                  DEFAULT FALSE,
    role_id         INTEGER             NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Platforms table
CREATE TABLE IF NOT EXISTS social_manager.platforms
(
    id                   SERIAL PRIMARY KEY,
    name                 VARCHAR(50)   NOT NULL UNIQUE,
    platform_type        platform_type NOT NULL,
    is_active            BOOLEAN DEFAULT TRUE,
    rate_limit_remaining INTEGER,
    rate_limit_reset_at  TIMESTAMP WITH TIME ZONE,
    last_sync            TIMESTAMP WITH TIME ZONE
);

-- Sources table (универсальная для всех типов контента)
CREATE TABLE IF NOT EXISTS social_manager.sources
(
    id           SERIAL PRIMARY KEY,
    platform_id  INTEGER REFERENCES social_manager.platforms (id),
    source_type  source_type  NOT NULL,
    external_id  VARCHAR(100) NOT NULL,
    name         VARCHAR(255),
    params       JSONB   DEFAULT '{}',
    is_active    BOOLEAN DEFAULT TRUE,
    last_checked TIMESTAMP WITH TIME ZONE,
    UNIQUE (platform_id, external_id)
);

-- Source-user relationships (many-to-many)
CREATE TABLE IF NOT EXISTS social_manager.source_user_relationships
(
    id        SERIAL PRIMARY KEY,
    source_id INTEGER REFERENCES social_manager.sources (id) ON DELETE CASCADE,
    user_id   INTEGER REFERENCES social_manager.sources (id) ON DELETE CASCADE,
    params    JSONB   DEFAULT '{}',
    is_active BOOLEAN DEFAULT TRUE,
    UNIQUE (source_id, user_id)
);

-- AI Analytics table (основная для хранения аналитики)
CREATE TABLE IF NOT EXISTS social_manager.ai_analytics
(
    id            SERIAL PRIMARY KEY,
    source_id     INTEGER REFERENCES social_manager.sources (id),
    analysis_date DATE  NOT NULL,
    period_type   VARCHAR(20)              DEFAULT 'daily',
    summary_data  JSONB NOT NULL,
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_id, analysis_date, period_type)
);

-- Bot scenarios table
CREATE TABLE IF NOT EXISTS social_manager.bot_scenarios
(
    id                 SERIAL PRIMARY KEY,
    name               VARCHAR(255) NOT NULL,
    trigger_conditions JSONB,
    ai_prompt          TEXT,
    is_active          BOOLEAN                  DEFAULT TRUE,
    cooldown_minutes   INTEGER                  DEFAULT 30,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Model types table
CREATE TABLE IF NOT EXISTS social_manager.model_types
(
    id          SERIAL PRIMARY KEY,
    app_name    VARCHAR(100) NOT NULL,
    model_name  VARCHAR(100) NOT NULL,
    table_name  VARCHAR(100) NOT NULL,
    description TEXT,
    is_managed  BOOLEAN DEFAULT TRUE,
    UNIQUE (app_name, model_name)
);

-- Permissions table
CREATE TABLE IF NOT EXISTS social_manager.permissions
(
    id            SERIAL PRIMARY KEY,
    codename      VARCHAR(100) NOT NULL,
    name          VARCHAR(200) NOT NULL,
    action_type   action_type  NOT NULL,
    model_type_id INTEGER      NOT NULL REFERENCES social_manager.model_types (id) ON DELETE CASCADE,
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (codename, model_type_id),
    CONSTRAINT check_codename_format CHECK (codename ~ '^[a-z_]+\.[a-z_]+\.[a-z]+$')
);

-- Roles table
CREATE TABLE IF NOT EXISTS social_manager.roles
(
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) UNIQUE NOT NULL,
    codename    user_role_type      NOT NULL,
    description TEXT,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Role-Permission many-to-many relationship
CREATE TABLE IF NOT EXISTS social_manager.role_permission
(
    role_id       INTEGER NOT NULL REFERENCES social_manager.roles (id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES social_manager.permissions (id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

-- Notifications table
CREATE TABLE IF NOT EXISTS social_manager.notifications
(
    id                  SERIAL PRIMARY KEY,
    title               VARCHAR(200) NOT NULL,
    message             TEXT         NOT NULL,
    notification_type   VARCHAR(50),
    is_read             BOOLEAN                  DEFAULT FALSE,
    related_entity_type VARCHAR(50),
    related_entity_id   INTEGER,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Add indexes for better performance
CREATE INDEX IF NOT EXISTS idx_users_email ON social_manager.users (email);
CREATE INDEX IF NOT EXISTS idx_users_username ON social_manager.users (username);
CREATE INDEX IF NOT EXISTS idx_users_role_id ON social_manager.users (role_id);

CREATE INDEX IF NOT EXISTS idx_platforms_name ON social_manager.platforms (name);
CREATE INDEX IF NOT EXISTS idx_platforms_type ON social_manager.platforms (platform_type);

CREATE INDEX IF NOT EXISTS idx_sources_platform_id ON social_manager.sources (platform_id);
CREATE INDEX IF NOT EXISTS idx_sources_type ON social_manager.sources (source_type);
CREATE INDEX IF NOT EXISTS idx_sources_external_id ON social_manager.sources (external_id);
CREATE INDEX IF NOT EXISTS idx_sources_last_checked ON social_manager.sources (last_checked);

CREATE INDEX IF NOT EXISTS idx_source_relationships_source ON social_manager.source_user_relationships (source_id);
CREATE INDEX IF NOT EXISTS idx_source_relationships_user ON social_manager.source_user_relationships (user_id);

CREATE INDEX IF NOT EXISTS idx_ai_analytics_source ON social_manager.ai_analytics (source_id);
CREATE INDEX IF NOT EXISTS idx_ai_analytics_date ON social_manager.ai_analytics (analysis_date);
CREATE INDEX IF NOT EXISTS idx_ai_analytics_period ON social_manager.ai_analytics (period_type);

CREATE INDEX IF NOT EXISTS idx_bot_scenarios_active ON social_manager.bot_scenarios (is_active);

-- Indexes for permissions system
CREATE INDEX IF NOT EXISTS idx_permissions_codename ON social_manager.permissions (codename);
CREATE INDEX IF NOT EXISTS idx_permissions_model_type_id ON social_manager.permissions (model_type_id);
CREATE INDEX IF NOT EXISTS idx_roles_codename ON social_manager.roles (codename);
CREATE INDEX IF NOT EXISTS idx_role_permission_role_id ON social_manager.role_permission (role_id);
CREATE INDEX IF NOT EXISTS idx_role_permission_permission_id ON social_manager.role_permission (permission_id);
CREATE INDEX IF NOT EXISTS idx_model_types_app_model ON social_manager.model_types (app_name, model_name);

CREATE INDEX IF NOT EXISTS idx_notifications_is_read ON social_manager.notifications (is_read);
CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON social_manager.notifications (created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_type ON social_manager.notifications (notification_type);

-- Table comments
COMMENT ON TABLE social_manager.users IS 'System users with authentication and authorization data';
COMMENT ON TABLE social_manager.platforms IS 'Social media platforms (secrets stored in env)';
COMMENT ON TABLE social_manager.sources IS 'Universal content sources (users, groups, channels, chats)';
COMMENT ON TABLE social_manager.source_user_relationships IS 'Many-to-many relationships between sources and monitored users';
COMMENT ON TABLE social_manager.ai_analytics IS 'AI analysis results and summaries';
COMMENT ON TABLE social_manager.bot_scenarios IS 'AI bot behavior scenarios for auto-commenting';
COMMENT ON TABLE social_manager.notifications IS 'User notifications';
COMMENT ON TABLE social_manager.model_types IS 'Content types for permission system';
COMMENT ON TABLE social_manager.permissions IS 'System permissions';
COMMENT ON TABLE social_manager.roles IS 'User roles with different permission levels';
COMMENT ON TABLE social_manager.role_permission IS 'Many-to-many relationship between roles and permissions';
