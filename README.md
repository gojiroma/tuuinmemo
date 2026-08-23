# 通院メモ

心療内科の通院時に主治医へ見せるための、エントリ一覧・Markdownメモ・睡眠/歩数グラフを一体化した個人用記録アプリ。編集用ページと主治医閲覧用ページはURLで分かれています。

## 構成

- `app.py` — Flask アプリ本体（`/`・`/view` のページ配信 + `/api/*` の全エンドポイント）
- `schema.sql` — Neon PostgreSQL 用スキーマ
- `templates/index.html` — 編集用フロントエンド（エントリ一覧・Markdownエディタ・グラフ）。`/`で配信
- `templates/view.html` — 主治医閲覧用の読み取り専用ページ（編集フォームなし、Markdownコピー用ボタン付き）。`/view`で配信
- `static/manifest.json` / `static/sw.js` / `static/icons/` — PWA（ホーム画面追加・簡易オフラインキャッシュ）。`sw.js`はスコープを`/`全体にするため`/sw.js`としても配信される
- `vercel.json` — 全リクエストを `app.py` に渡す設定（静的ファイル配信もFlask内部のstatic/templateで処理）
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
python3 app.py   # http://127.0.0.1:5000
```

### 3. Vercel にデプロイ

```bash
vercel
```

Vercel プロジェクトの環境変数に `DATABASE_URL` を設定してください。

`/` は編集用、`/view` は主治医に見せる読み取り専用ページです（同じ非公開URL運用・認証なしの想定。`/view`はフォームや削除ボタンがなく、各記録に「Markdownをコピー」ボタンがあります）。

## API

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/api/memo?date=YYYY-MM-DD` | 指定日のメモ取得 |
| GET | `/api/memo?limit=200` | 直近N件のメモ一覧（エントリ一覧用、デフォルト200件・最大500件） |
| POST | `/api/memo` | メモの作成/更新（日付でupsert） |
| GET / PUT / DELETE | `/api/memo/<id>` | ID指定での取得・更新・削除 |
| GET | `/api/vitals?start=YYYY-MM-DD&end=YYYY-MM-DD` | 睡眠・歩数データ取得（省略時は直近31日） |
| POST | `/api/vitals/import-csv` | CSVインポート（`type=sleep`\|`steps`, `file`） |

CSVは `date`/`day`/`timestamp` 列と `duration`/`sleep`(分)/`steps` 列を、大文字小文字を問わず部分一致で自動検出します（例: `Timestamp` + `Steps (steps)` / `Timestamp` + `Sleep duration`）。

バイタルグラフは「直近31日」「週次」「月次」を切り替えられます。週次・月次は範囲内の平均値（歩数は平均歩数/日、睡眠は平均時間/日）を表示し、通院日を含む期間はオレンジ色でハイライトされます。

## メモ

- Markdownの4セクション（生活変化・体調・今後の意向・質問相談依頼）はエディタの「4セクション挿入」ボタンで挿入されるテンプレートで、強制ではありません。
- オフライン時は入力内容を `localStorage` に下書き保存し、オンライン復帰時に自動同期します。
- `/api/cleanup` のような自動削除機能は、実データ喪失リスクがあるため今回は未実装です。
