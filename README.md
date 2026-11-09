# INMU KING 動画モニター（独立ホスティング版）

ニコニコ動画とYouTubeから、タイトルに「INMU KING」を含む動画を自動収集して一覧表示する、非公式ファンツールの公開ページです。
GitHub Pages で公開し、GitHub Actions が15分ごとにデータを自動更新します。サーバー代はかかりません（GitHub の無料枠で動きます）。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `index.html` | 公開ページ本体。`data.json` と `history.json` を読み込んで表示します |
| `data.json` | 動画一覧・特設の現在値・収集状況（自動更新されます） |
| `history.json` | 特設動画（修正版MV）の再生数などの推移記録（自動で追記されます） |
| `update.py` | 収集スクリプト。ニコニコ公式APIとYouTube Data API v3から取得します |
| `.github/workflows/update.yml` | 15分ごとの自動実行の設定 |

## セットアップ手順

### 1. GitHubアカウントを作る（持っている人は飛ばす）

1. https://github.com を開き、「Sign up」からアカウントを作成します（無料）。

### 2. 新しいリポジトリを作る

1. ログイン後、右上の「＋」→「New repository」を選びます。
2. Repository name に好きな名前を入れます（例：`inmuking-monitor`）。
3. **Public** を選んで「Create repository」を押します。
   - ※ GitHub Pages の無料公開には Public が必要です。公開されるのはこのモニターのページとデータだけで、APIキーは含まれません（キーは手順4で別の場所に登録します）。

### 3. このパッケージのファイルをアップロードする

1. 作成したリポジトリのページで「uploading an existing file」を押します（無ければ「Add file」→「Upload files」）。
2. このパッケージの中身（`index.html`、`data.json`、`history.json`、`update.py`、`README.md`）をすべてドラッグ＆ドロップし、「Commit changes」を押します。
3. **`.github` フォルダも忘れずにアップロードしてください。** 自動更新の設定（`.github/workflows/update.yml`）が入っています。
   - `.github` は隠しフォルダとして見えている場合があります。エクスプローラー／Finderで隠しファイル表示を有効にしてください。
   - まとめてアップロードできない場合は、リポジトリ上で「Add file」→「Create new file」でファイル名に `.github/workflows/update.yml` と入力するとフォルダごと作れます。中身はパッケージ内の同名ファイルと同じ内容を貼り付けてください。

### 4. YouTube APIキーを登録する（Secrets）

1. リポジトリの「Settings」→ 左メニューの「Secrets and variables」→「Actions」を開きます。
2. 「New repository secret」を押し、Name に `YOUTUBE_API_KEY`、Secret に **既存の Google Cloud で使っている YouTube Data API v3 のキー** を入力して「Add secret」を押します。
3. **注意：キーをチャット・X・スクリーンショットなどに貼らないでください。** この Secrets に登録したキーは、リポジトリのファイルには保存されず、公開ページからも見えません。
4. キーを登録しない場合もページは動きますが、YouTube の収集が止まり、ニコニコだけの更新になります。

### 5. GitHub Pages を有効にする

1. リポジトリの「Settings」→ 左メニューの「Pages」を開きます。
2. 「Build and deployment」の Source で「Deploy from a branch」を選び、Branch は **main** ／ **(root)** にして「Save」を押します。
3. 数分待つと、同じ画面に公開URLが表示されます。

### 6. 初回の更新を手動で実行する

1. リポジトリの「Actions」タブを開き、左の「Update monitor data」を選びます。
2. 「Run workflow」→「Run workflow」を押します。
3. 1〜2分で緑のチェックが付けば成功です。`data.json` と `history.json` に「Update monitor data」という自動コミットが追加されます。

### 7. 公開URLを確認する

- 公開URLは **`https://（あなたのユーザー名）.github.io/（リポジトリ名）/`** です。
- 例：ユーザー名が `taro`、リポジトリ名が `inmuking-monitor` なら `https://taro.github.io/inmuking-monitor/`
- このURLが、Xなどで共有するリンクになります。

## 運用メモ

- **自動更新は15分ごと**です（GitHub Actions のスケジュール実行のため、混雑時は数分遅れることがあります）。更新のたびにデータファイルへ自動コミットが1件追加されます。
- **YouTube 検索クォータの注意**：YouTube Data API には1日あたりの上限（検索1回あたりの消費が大きい）があります。上限に達すると、その日は YouTube の収集だけが止まり、翌日（米国太平洋時間の深夜＝日本時間の夕方ごろ）に回復します。ニコニコの収集とページ表示は影響を受けません。モニター本体側でも同じキーを使っている場合は、クォータを分け合う点にも注意してください。
- 特設（修正版MV）の再生数グラフは、YouTube の収集が動いている間だけ点が増えます。
- 「NEW」バッジは、前回のデータになかった動画に付きます。上部の「前回の公開から新着 ◯件」がその件数です。
- 収集条件は「タイトルに INMU KING / INMU_KING / INMUKING を含む動画のみ」です。タグや説明文だけが一致する動画は、ノイズ対策のため採用していません。

## Muse 側の旧公開版との関係

- このページは Muse の公開版から移行した独立版です。移行が完了したら、Muse 側の旧公開リンクは古い時点のデータのまま止まります（自動差し替えは停止します）。
- 共有するリンクは、今後はこの GitHub Pages のURLを使ってください。
- モニター本体（非公開の収集スペース）は Muse 側に残ります。この独立版は公開専用で、本体とは別に自分で収集します。
