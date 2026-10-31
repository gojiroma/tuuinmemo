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
-- "memo_" 接頭辞は、同じDBに存在する無関係な search_history テーブルとの
-- 名前衝突を避けるため（CREATE TABLE IF NOT EXISTS がそちらを既存テーブル
-- とみなしてスキップしてしまう事故があったため）。
CREATE TABLE IF NOT EXISTS memo_search_history (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    query text NOT NULL UNIQUE,
    searched_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memo_search_history_searched_at ON memo_search_history (searched_at DESC);

-- ゲスト閲覧リンク（管理者が発行する期限付き・閲覧専用トークン）
CREATE TABLE IF NOT EXISTS guest_links (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_guest_links_token ON guest_links (token);

-- 主治医の端末で入力する短い共有コード（10分以内・1回だけ使える。使うとトークンと交換して消える）
ALTER TABLE guest_links ADD COLUMN IF NOT EXISTS short_code text UNIQUE;
ALTER TABLE guest_links ADD COLUMN IF NOT EXISTS short_code_expires_at timestamptz;

-- 服薬期間（名前と開始日〜終了日。終了日が空なら継続中）
CREATE TABLE IF NOT EXISTS memo_medications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    start_date date NOT NULL,
    end_date date,
    created_at timestamptz NOT NULL DEFAULT now()
);
