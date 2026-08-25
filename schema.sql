-- 医療記録アプリ（通院メモ）DB スキーマ
-- Neon PostgreSQL 用。 psql "$DATABASE_URL" -f schema.sql で実行。

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- メモ管理（1日1件）
CREATE TABLE IF NOT EXISTS memos (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    date date NOT NULL UNIQUE,
    is_clinic_day boolean NOT NULL DEFAULT true, -- kept for schema compat; API now always reports true (an entry existing = a clinic day)
    summary text,               -- サマリ（短い要約）
    content text,                -- Markdown コンテンツ
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memos_date ON memos (date);
CREATE INDEX IF NOT EXISTS idx_memos_clinic_day ON memos (is_clinic_day) WHERE is_clinic_day;

-- 睡眠データ（CSVインポート、1日1件）
CREATE TABLE IF NOT EXISTS sleep_data (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    date date NOT NULL UNIQUE,
    duration integer NOT NULL   -- 分単位
);

CREATE INDEX IF NOT EXISTS idx_sleep_data_date ON sleep_data (date);

-- 歩数データ（CSVインポート、1日1件）
CREATE TABLE IF NOT EXISTS steps_data (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    date date NOT NULL UNIQUE,
    steps integer NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_steps_data_date ON steps_data (date);

-- メモ編集履歴（十分な間隔を空けた上書き時にだけ直前バージョンをスナップショット）
CREATE TABLE IF NOT EXISTS memo_history (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    memo_id uuid NOT NULL REFERENCES memos(id) ON DELETE CASCADE,
    date date NOT NULL,
    summary text,
    content text,
    archived_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memo_history_memo_id ON memo_history (memo_id, archived_at DESC);

-- キーワード検索履歴（直近使った検索語をサジェスト用に保存）
CREATE TABLE IF NOT EXISTS search_history (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    query text NOT NULL UNIQUE,
    searched_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_search_history_searched_at ON search_history (searched_at DESC);
