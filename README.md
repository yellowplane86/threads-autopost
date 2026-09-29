# Threads 自動投稿パイプライン

毎晩のルーティンが生成した投稿パッケージを、GitHub Actions が Threads に自動投稿します。
承認ステップはありません。代わりに**投稿直前ガード**が「投稿直前チェックリスト」を機械的に実行し、
1つでも外れた枠は投稿しません（fail-closed）。

```
毎日20:00(現地)  ルーティン → posts/YYYY-MM-DD.json を commit
07:30 / 12:15 / 18:30 / 21:30 JST  Actions cron → ガード → Threads API
```

Xは `scripts/platforms/x.py` を足して `platforms/__init__.py` の `PLATFORMS` に1行加えるだけで載ります。
投稿データ（`posts/*.json`）は共通なので作り直しは不要です。

---

## 1. Threadsに接続する

Threads APIは**管理画面から直接トークンを発行できません**。自分のアカウントに
自分で投稿するだけの用途でも、OAuth 2.0 の認可フローを通す必要があります。
`scripts/connect.py` がその4ステップを補助します。

> ドメインについて：公式ドキュメントは `threads.com` / `graph.threads.com`（新）と
> `threads.net` / `graph.threads.net`（旧）が混在しています。`connect.py` は
> **両方を順に試して、通った方を教えます**。

### 1-1. アプリを作る（ブラウザ作業）

1. [Meta for Developers](https://developers.facebook.com/) でアプリを作成し、
   作成時に **Threads** のユースケースを選ぶ
2. アプリ作成時にIDとシークレットが2組生成されます。**Threads用のApp IDとApp Secret**を使ってください
   （App Dashboard → App settings → Basic）
3. 権限に `threads_basic` と `threads_content_publish` を追加
4. **有効なOAuthリダイレクトURI**に、これから使うURIを登録する（例 `https://localhost/callback`）
5. App Dashboard で自分のThreadsアカウントを**テスターとして招待し、承認**する

> 自分のアカウントにだけ投稿する用途なら、App Review（アプリ審査）は不要です。
> テスターとして承認済みであれば権限が使えます。

### 1-2. 認可URLを開いて認可コードを取る

```bash
python scripts/connect.py authurl \
  --app-id <THREADS_APP_ID> \
  --redirect-uri https://localhost/callback
```

出力されたURLをブラウザで開いて承認します。`https://localhost/callback` は
**開けなくて構いません**。アドレスバーに戻ってきたURLから `code=` の値をコピーします
（末尾に `#_` が付いていたら、それは含めません）。

### 1-3. トークンに交換する

```bash
# 認可コード -> 短期トークン（1時間有効）
python scripts/connect.py exchange \
  --app-id <ID> --app-secret <SECRET> \
  --redirect-uri https://localhost/callback --code <CODE>

# 短期 -> 長期トークン（60日有効）
python scripts/connect.py longlived \
  --app-secret <SECRET> --token <SHORT_LIVED_TOKEN>
```

`exchange` の出力に `user_id` も含まれます。これが `THREADS_USER_ID` です。

### 1-4. 接続を確認する

```bash
python scripts/connect.py check --user-id <ID> --token <LONG_LIVED_TOKEN>
```

確認内容は3つです。**公開投稿は一切しません。**

- `/me` が通るか（トークンと `threads_basic`）
- 返ってきたIDが `THREADS_USER_ID` と一致するか
- 未公開コンテナを1つ作れるか（`threads_content_publish` の確認。
  publishしないので外からは見えず、24時間で期限切れになります）

Secretsを入れたあとは、GitHub側から実行しても同じ確認ができます。
**Actions → Threads connection test → Run workflow**

### 1-5. リポジトリSecretsを設定

Settings → Secrets and variables → Actions

| Secret | 中身 |
|---|---|
| `THREADS_USER_ID` | 上で取得したユーザーID |
| `THREADS_ACCESS_TOKEN` | 長期アクセストークン |
| `GH_PAT` | このリポジトリの Secrets に書き込めるPAT（トークン自動更新用） |

`connect.py check` が `graph.threads.net` 側で通った場合のみ、
ワークフローの `env` に `THREADS_GRAPH_HOST: graph.threads.net` を追加してください
（既定は `graph.threads.com`）。

`GH_PAT` は fine-grained PAT で、このリポジトリに対して
**Secrets: Read and write** と **Metadata: Read** を付けてください。

### 1-3. トークンの寿命に注意

長期トークンは**60日で失効し、失効すると更新できません**。
`refresh-token.yml` が毎月1日と15日に自動更新してSecretを書き換えます。
このワークフローが2回連続で失敗したら、手動で取り直す必要があります。
**失敗通知が届く設定になっているか必ず確認してください。**

---

## 2. 毎日の運用で人がやること

完全自動ですが、**2つだけ機械が用意できないもの**があります。
これが入っていない枠は、ガードが止めて投稿されません。

### 2-1. アフィリエイトURL

`link_url` は空で生成されます。架空URLを作らない方針のためです。

```bash
python scripts/build_reply.py posts/2026-09-29.json \
  --set morning=https://... \
  --set noon=https://... \
  --set evening=https://... \
  --set night=https://...
```

`build_reply.py` が 固定の形（`料金・空室はこちら👇pr` → URL）で返信を組み立てます。

### 2-2. 画像URL

Threads APIは画像を**公開URLから取得**します。ASP提供素材を公開リポジトリに置くと
広告主の素材利用規定に触れる可能性があるため、**有効期限付きの署名URL**を推奨します。

- R2 / S3 に非公開バケットを用意し、`YYYY-MM-DD/<slot>.jpg` で置く
- 投稿直前に2時間有効の署名URLを発行して `image_url` に入れる

`require_image: true` の枠で画像が用意できなければ、**テキストのみでの代替投稿はせず枠を落とします**
（指示書の「画像が用意できない案件は落とす」に従っています）。

---

## 3. 投稿直前ガード（承認の代わり）

`scripts/guards.py` が実行する内容です。

| ガード | 内容 | 外れたとき |
|---|---|---|
| 字数 | 本文・返信が500字以内（改行も1字） | 停止 |
| PR表記 | 本文にPR表記がない／返信が固定の形 `料金・空室はこちら👇pr` + URL と完全一致 | 停止 |
| URL位置 | 本文にURLを含まない／返信内のURLがちょうど1本で `link_url` と一致 | 停止 |
| 賞味期限 | `expires_on` を過ぎていない（例: 雲海テラス 2026-10-13） | 停止 |
| 画像到達性 | `image_url` がhttpsで200を返す | 停止 |
| 一次ソース生存 | `source_checks` のURLが200 かつ `must_contain` を満たす | 停止 |
| 二重投稿 | `state/posted.json` に記録済みなら投稿しない | スキップ |

`source_checks` は運休・営業終了を検知するための仕掛けです。例:

```json
{ "url": "https://shinhotaka-ropeway.jp/", "must_contain": ["営業中"] }
```

公式サイトから「営業中」が消えたら運休の可能性がある、という読みです。

> **初回に必ずやること**：この文字列判定が実際に機能するかは、対象サイトのHTMLが
> JavaScriptで描画されているかに依存します。**必ず一度 dry-run で確認してください。**
> Actions → Post to Threads → Run workflow → `dry_run: true`
>
> 判定が空振りする場合は `must_contain` を、HTMLに確実に含まれる別の文字列に差し替えてください。

---

## 3-B. GitHubを使わず手元のPCで動かす場合

`post.py` はGitHub固有の機能に依存していないので、手元のPCのcronからも動きます。
ただし**PCがスリープ・電源オフ・ネット未接続だと、その枠は投稿されません**。
ノートPCだとまず取りこぼすので、常時起動の機械がない場合はGitHub Actionsを勧めます。

```bash
# 環境変数を用意（.env はgit管理外）
export THREADS_USER_ID=...
export THREADS_ACCESS_TOKEN=...

# 1枠を投稿
python scripts/post.py --slot auto
```

**macOS / Linux の crontab**（cronはローカル時刻で動くので、PCのタイムゾーンに注意）

```cron
# JSTのPCなら、そのままこの時刻でよい
30 7  * * * cd ~/threads-autopost && /usr/bin/python3 scripts/post.py --slot morning --no-wait >> ~/threads-autopost.log 2>&1
15 12 * * * cd ~/threads-autopost && /usr/bin/python3 scripts/post.py --slot noon    --no-wait >> ~/threads-autopost.log 2>&1
30 18 * * * cd ~/threads-autopost && /usr/bin/python3 scripts/post.py --slot evening --no-wait >> ~/threads-autopost.log 2>&1
30 21 * * * cd ~/threads-autopost && /usr/bin/python3 scripts/post.py --slot night   --no-wait >> ~/threads-autopost.log 2>&1
```

PCがベトナム時間（UTC+7）なら、JSTの2時間前に書き換えます（05:30 / 10:15 / 16:30 / 19:30）。
`--no-wait` を外すと、`post.py` がJSTの目標時刻まで待ってから投稿します。

トークンの60日更新も自分でやる必要があります（`connect.py longlived` を再実行するか、
`refresh_access_token` を月2回叩くcronを足してください）。

---

## 4. 投稿時刻の精度

GitHub Actionsのcronは分単位で遅延します（数分〜十数分）。
そのため各枠の**12分前に起動し、`post.py` が目標時刻まで待機**します（最大20分）。
起動が20分以上遅れた場合は、待機せず即時投稿します。

| 枠 | JST | cron (UTC) |
|---|---|---|
| morning | 07:30 | `18 22 * * *`（前日） |
| noon | 12:15 | `3 3 * * *` |
| evening | 18:30 | `18 9 * * *` |
| night | 21:30 | `18 12 * * *` |

---

## 5. 手元での確認

```bash
# 構造検査（ネットワーク不要）
python scripts/validate.py

# ガードまで走らせて投稿しない
python scripts/post.py --slot noon --date 2026-09-29 --dry-run --no-wait
```

---

## 6. 通知

ガードで停止した枠は、ワークフローを**意図的に失敗させます**。
無人運用では「正常に止めた」を静かに済ませると気づけないためです。
GitHubの通知設定で、Actionsの失敗がメールまたはモバイルに届くようにしてください。

---

## 7. ディレクトリ

```
posts/YYYY-MM-DD.json   その日の4枠（ルーティンが生成）
schema/post.schema.json 上記の形式定義
scripts/post.py         1枠を投稿
scripts/guards.py       投稿直前ガード
scripts/validate.py     構造検査（CI）
scripts/build_reply.py  link_url から返信を生成
scripts/platforms/      Threads実装。Xはここに追加
state/posted.json       投稿済み記録（二重投稿防止）
```
