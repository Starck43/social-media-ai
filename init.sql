-- Create schema
CREATE SCHEMA IF NOT EXISTS social_manager;

-- Enable required extensions

-- Create ENUM types
CREATE TYPE action_type AS ENUM ('VIEW', 'CREATE', 'UPDATE', 'DELETE', 'ANALYZE', 'MODERATE', 'EXPORT', 'CONFIGURE');
CREATE TYPE user_role_type AS ENUM ('VIEWER', 'AI_BOT', 'MANAGER', 'ANALYST', 'MODERATOR', 'ADMIN', 'SUPERUSER');
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Users table
CREATE TABLE IF NOT EXISTS social_manager.users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    hashed_password VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    is_superuser BOOLEAN DEFAULT FALSE,
    role_id INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_users_role_id FOREIGN KEY (role_id) 
        REFERENCES social_manager.roles(id) ON DELETE RESTRICT
);

-- Таблица социальных аккаунтов
CREATE TABLE IF NOT EXISTS social_manager.social_accounts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES social_manager.users(id) ON DELETE CASCADE,
    platform VARCHAR(20) NOT NULL,
    platform_user_id VARCHAR(100) NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    token_expires_at TIMESTAMP WITH TIME ZONE,
    profile_data JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(platform, platform_user_id)
);

-- Таблица социальных групп
CREATE TABLE IF NOT EXISTS social_manager.social_groups (
    id SERIAL PRIMARY KEY,
    social_account_id INTEGER REFERENCES social_manager.social_accounts(id) ON DELETE CASCADE,
    platform VARCHAR(20) NOT NULL,
    platform_group_id VARCHAR(100) NOT NULL,
    name VARCHAR(200) NOT NULL,
    screen_name VARCHAR(100),
    photo_url TEXT,
    members_count INTEGER DEFAULT 0,
    is_tracking BOOLEAN DEFAULT TRUE,
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(platform, platform_group_id)
);

-- Таблица постов
CREATE TABLE IF NOT EXISTS social_manager.posts (
    id SERIAL PRIMARY KEY,
    group_id INTEGER REFERENCES social_manager.social_groups(id) ON DELETE CASCADE,
    platform_post_id VARCHAR(100) NOT NULL,
    text TEXT,
    likes_count INTEGER DEFAULT 0,
    comments_count INTEGER DEFAULT 0,
    reposts_count INTEGER DEFAULT 0,
    views_count INTEGER DEFAULT 0,
    post_date TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(group_id, platform_post_id)
);

-- Таблица комментариев
CREATE TABLE IF NOT EXISTS social_manager.comments (
    id SERIAL PRIMARY KEY,
    post_id INTEGER REFERENCES social_manager.posts(id) ON DELETE CASCADE,
    platform_comment_id VARCHAR(100) NOT NULL,
    author_id VARCHAR(100),
    author_name VARCHAR(200),
    text TEXT,
    likes_count INTEGER DEFAULT 0,
    sentiment_score DOUBLE PRECISION,
    sentiment_label VARCHAR(20),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    comment_date TIMESTAMP WITH TIME ZONE,
    UNIQUE(post_id, platform_comment_id)
);

-- Таблица статистики
CREATE TABLE IF NOT EXISTS social_manager.statistics (
    id SERIAL PRIMARY KEY,
    group_id INTEGER REFERENCES social_manager.social_groups(id) ON DELETE CASCADE,
    date TIMESTAMP WITH TIME ZONE NOT NULL,
    metrics JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(group_id, date)
);

-- Таблица результатов AI анализа (НОВАЯ)
CREATE TABLE IF NOT EXISTS social_manager.ai_analysis_results (
    id SERIAL PRIMARY KEY,
    group_id INTEGER REFERENCES social_manager.social_groups(id) ON DELETE CASCADE,
    analysis_type VARCHAR(50) NOT NULL,
    period_start TIMESTAMP WITH TIME ZONE NOT NULL,
    period_end TIMESTAMP WITH TIME ZONE NOT NULL,
    results JSONB NOT NULL,
    confidence_score DOUBLE PRECISION,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Model types table (for content types)
CREATE TABLE IF NOT EXISTS social_manager.model_types (
    id SERIAL PRIMARY KEY,
    app_name VARCHAR(100) NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    table_name VARCHAR(100) NOT NULL,
    description TEXT,
    is_managed BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(app_name, model_name)
);

-- Permissions table
CREATE TABLE IF NOT EXISTS social_manager.permissions (
    id SERIAL PRIMARY KEY,
    codename VARCHAR(100) NOT NULL,
    name VARCHAR(200) NOT NULL,
    action_type action_type NOT NULL,
    model_type_id INTEGER NOT NULL REFERENCES social_manager.model_types(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(codename, model_type_id),
    CONSTRAINT check_codename_format CHECK (codename ~ '^[a-z_]+\\.[a-z_]+\\.[a-z]+$')
);

-- Roles table
CREATE TABLE IF NOT EXISTS social_manager.roles (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    codename user_role_type NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Role-Permission many-to-many relationship
CREATE TABLE IF NOT EXISTS social_manager.role_permission (
    role_id INTEGER NOT NULL REFERENCES social_manager.roles(id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES social_manager.permissions(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (role_id, permission_id)
);

-- Add indexes for better performance
CREATE INDEX IF NOT EXISTS idx_users_email ON social_manager.users(email);
CREATE INDEX IF NOT EXISTS idx_users_username ON social_manager.users(username);
CREATE INDEX IF NOT EXISTS idx_users_role_id ON social_manager.users(role_id);

-- Indexes for permissions
CREATE INDEX IF NOT EXISTS idx_permissions_codename ON social_manager.permissions(codename);
CREATE INDEX IF NOT EXISTS idx_permissions_model_type_id ON social_manager.permissions(model_type_id);

-- Indexes for roles
CREATE INDEX IF NOT EXISTS idx_roles_codename ON social_manager.roles(codename);

-- Index for role_permission
CREATE INDEX IF NOT EXISTS idx_role_permission_role_id ON social_manager.role_permission(role_id);
CREATE INDEX IF NOT EXISTS idx_role_permission_permission_id ON social_manager.role_permission(permission_id);

-- Index for model_types
CREATE INDEX IF NOT EXISTS idx_model_types_app_model ON social_manager.model_types(app_name, model_name);

-- Таблица уведомлений (НОВАЯ)
CREATE TABLE IF NOT EXISTS social_manager.notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES social_manager.users(id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    message TEXT NOT NULL,
    notification_type VARCHAR(50),
    is_read BOOLEAN DEFAULT FALSE,
    related_entity_type VARCHAR(50),
    related_entity_id INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Индексы для улучшения производительности
CREATE INDEX IF NOT EXISTS idx_users_email ON social_manager.users(email);
CREATE INDEX IF NOT EXISTS idx_users_username ON social_manager.users(username);

CREATE INDEX IF NOT EXISTS idx_social_accounts_user_id ON social_manager.social_accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_social_accounts_platform ON social_manager.social_accounts(platform);
CREATE INDEX IF NOT EXISTS idx_social_accounts_platform_user ON social_manager.social_accounts(platform, platform_user_id);

CREATE INDEX IF NOT EXISTS idx_social_groups_social_account_id ON social_manager.social_groups(social_account_id);
CREATE INDEX IF NOT EXISTS idx_social_groups_platform ON social_manager.social_groups(platform);
CREATE INDEX IF NOT EXISTS idx_social_groups_platform_group ON social_manager.social_groups(platform, platform_group_id);

CREATE INDEX IF NOT EXISTS idx_posts_group_id ON social_manager.posts(group_id);
CREATE INDEX IF NOT EXISTS idx_posts_platform_post ON social_manager.posts(group_id, platform_post_id);
CREATE INDEX IF NOT EXISTS idx_posts_post_date ON social_manager.posts(post_date);

CREATE INDEX IF NOT EXISTS idx_comments_post_id ON social_manager.comments(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_sentiment ON social_manager.comments(sentiment_score);
CREATE INDEX IF NOT EXISTS idx_comments_comment_date ON social_manager.comments(comment_date);

CREATE INDEX IF NOT EXISTS idx_statistics_group_id ON social_manager.statistics(group_id);
CREATE INDEX IF NOT EXISTS idx_statistics_date ON social_manager.statistics(date);

CREATE INDEX IF NOT EXISTS idx_ai_analysis_group_id ON social_manager.ai_analysis_results(group_id);
CREATE INDEX IF NOT EXISTS idx_ai_analysis_type ON social_manager.ai_analysis_results(analysis_type);
CREATE INDEX IF NOT EXISTS idx_ai_analysis_period ON social_manager.ai_analysis_results(period_start, period_end);

CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON social_manager.notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_is_read ON social_manager.notifications(is_read);
CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON social_manager.notifications(created_at);

-- Table comments
COMMENT ON TABLE social_manager.users IS 'System users with authentication and authorization data';
COMMENT ON TABLE social_manager.social_accounts IS 'Connected social media accounts';
COMMENT ON TABLE social_manager.social_groups IS 'Social media communities and groups';
COMMENT ON TABLE social_manager.posts IS 'Social media posts';
COMMENT ON TABLE social_manager.comments IS 'Post comments';
COMMENT ON TABLE social_manager.statistics IS 'Community statistics';
COMMENT ON TABLE social_manager.ai_analysis_results IS 'AI analysis results';
COMMENT ON TABLE social_manager.notifications IS 'User notifications';
COMMENT ON TABLE social_manager.model_types IS 'Content types for permission system';
COMMENT ON TABLE social_manager.permissions IS 'System permissions';
COMMENT ON TABLE social_manager.roles IS 'User roles with different permission levels';
COMMENT ON TABLE social_manager.role_permission IS 'Many-to-many relationship between roles and permissions';
