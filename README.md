# 通院メモ

心療内科の通院時に主治医へ見せるための、カレンダー・Markdownメモ・睡眠/歩数グラフを一体化した個人用記録アプリ。

## 構成

- `api/index.py` — Flask アプリ本体（`/api/*` の全エンドポイント）
- `schema.sql` — Neon PostgreSQL 用スキーマ
- `index.html` — フロントエンド（カレンダー・Markdownエディタ・グラフ）
- `manifest.json` / `sw.js` / `icons/` — PWA（ホーム画面追加・簡易オフラインキャッシュ）
- `vercel.json` — `/api/*` を Flask に、それ以外を静的ファイルとして配信する設定
- `requirements.txt` — Python 依存パッケージ

## セットアップ

### 1. Neon で DB を用意

Neon でプロジェクトを作成し、接続文字列（`postgresql://...`）を取得します。

```bash
psql "$DATABASE_URL" -f schema.sql
```

### 2. ローカルで動作確認

```bash
pip install -r requirements.txt
export DATABASE_URL="postgresql://user:pass@host/db"
python3 api/index.py   # http://127.0.0.1:5000
```

`index.html` を同じオリジンから配信する場合は、`python3 -m http.server` 等で別途配信するか、
そのまま `api/index.py` にリクエストしつつブラウザで `index.html` を直接開いて `fetch` 先を調整してください。
（Vercel 上ではどちらも同一オリジンになるため、この調整は不要です。）

### 3. Vercel にデプロイ

```bash
vercel
```

Vercel プロジェクトの環境変数に `DATABASE_URL` を設定してください。

デプロイ後の URL はそのまま主治医への共有リンクとしても使えます（非公開URL運用・認証なし・書き込み可の想定）。

## API

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/api/memo?date=YYYY-MM-DD` | 指定日のメモ取得 |
| GET | `/api/memo?year=&month=` | 月別メモ一覧 |
| POST | `/api/memo` | メモの作成/更新（日付でupsert） |
| GET / PUT / DELETE | `/api/memo/<id>` | ID指定での取得・更新・削除 |
| GET | `/api/vitals?year=&month=` | 睡眠・歩数データ取得 |
| POST | `/api/vitals/import-csv` | CSVインポート（`type=sleep`\|`steps`, `file`） |

CSVは `date`/`day` 列と `duration`(分)/`steps` 列を大文字小文字問わず自動検出します。

## メモ

- Markdownの4セクション（生活変化・体調・今後の意向・質問相談依頼）はエディタの「4セクション挿入」ボタンで挿入されるテンプレートで、強制ではありません。
- オフライン時は入力内容を `localStorage` に下書き保存し、オンライン復帰時に自動同期します。
- `/api/cleanup` のような自動削除機能は、実データ喪失リスクがあるため今回は未実装です。
