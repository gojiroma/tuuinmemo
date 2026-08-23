# 通院メモ

心療内科の通院時に主治医へ見せるための、エントリ一覧・Markdownメモ・睡眠/歩数グラフを一体化した個人用記録アプリ。編集用ページと主治医閲覧用ページはURLで分かれています。

## 構成

- `app.py` — Flask アプリ本体（`/`・`/edit` のページ配信 + `/api/*` の全エンドポイント）
- `schema.sql` — Neon PostgreSQL 用スキーマ
- `templates/index.html` — 編集用フロントエンド（エントリ一覧・Markdownエディタ・グラフ）。`/edit`で配信
- `templates/view.html` — 主治医閲覧用の読み取り専用ページ（編集フォームなし、コピー・グラフ画像ダウンロードボタン付き）。ルート`/`で配信
- `static/theme.css` — 両ページ共通のデザイントークン・カード/ボタン/idle fade などの共通CSS
- `static/idle-ui.js` — 無操作が続くと副次的なUI（ナビリンクや期間トグルなど）を減光させる共通スクリプト
- `static/vitals-chart.js` — 睡眠・歩数グラフの日付バケット化とChart.js描画ロジック（編集/閲覧ページ共通）
- `static/markdown-editor.js` — Markdownエディタの箇条書き・見出し自動補完、Tabインデント、Alt+↑/↓行入れ替え
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

ルート`/`は主治医に見せる読み取り専用ページ、`/edit`が編集用ページです（同じ非公開URL運用・認証なしの想定）。`/`は`/edit`と同じく「左に日付＋サマリーの一覧、右に選択した記録の全文」という2ペイン構成で、フォームや削除ボタンはなく「メモをコピー」ボタンのみがあります。両ページのヘッダーからお互いに移動できます。PWAとしてホーム画面に追加した場合の起動先は`/edit`です。

## API

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/api/memo?date=YYYY-MM-DD` | 指定日のメモ取得 |
| GET | `/api/memo?limit=200` | 直近N件のメモ一覧（エントリ一覧用、デフォルト200件・最大500件） |
| POST | `/api/memo` | メモの作成/更新（日付でupsert） |
| GET / PUT / DELETE | `/api/memo/<id>` | ID指定での取得・更新・削除 |
| GET | `/api/vitals?start=YYYY-MM-DD&end=YYYY-MM-DD` | 睡眠・歩数データ取得（省略時は直近50日） |
| POST | `/api/vitals/import-csv` | CSVインポート（`type=sleep`\|`steps`, `file`） |

CSVは `date`/`day`/`timestamp` 列と `duration`/`sleep`(分)/`steps` 列を、大文字小文字を問わず部分一致で自動検出します（例: `Timestamp` + `Steps (steps)` / `Timestamp` + `Sleep duration`）。

バイタルグラフは「直近50日」「直近100日」「週次」「月次」を切り替えられます。週次・月次は範囲内の平均値（歩数は平均歩数/日、睡眠は平均時間/日）を表示し、月次は2025年12月を起点の固定範囲です。通院日を含む期間はオレンジ色でハイライトされ、凡例にも「通院日」のスウォッチが表示されます。「直近50日/100日」表示では通院日にカーソルを合わせるとツールチップにその日のメモ（サマリー・本文冒頭）が表示されます。「通院日」はエントリが存在する日を自動的にそう扱う仕様で、`is_clinic_day` は常に `true` を返します（別途チェックボックスでの指定はありません）。

## メモ

- Markdownの4セクション（生活変化・体調・今後の意向・質問相談依頼）はエディタの「4セクション挿入」ボタンで挿入されるテンプレートで、強制ではありません。
- Markdownエディタは「`- `で箇条書き継続」「`#`で見出し自動補完」「Tab/Shift+Tabでインデント」「Alt+↑/↓で行入れ替え」に対応しています（`static/markdown-editor.js`）。
- 本文中の `![alt](URL)` 形式の画像は「画像を結合保存」ボタンで縦に1枚のPNGへ結合してダウンロードできます。
- サマリ・本文は入力停止後に自動保存されます（保存ボタンはありません）。オフライン時は `localStorage` に下書き保存し、オンライン復帰時に自動同期します。
- エントリの削除はエントリ一覧の各行にある🗑ボタンから行います。
- `/api/cleanup` のような自動削除機能は、実データ喪失リスクがあるため今回は未実装です。
