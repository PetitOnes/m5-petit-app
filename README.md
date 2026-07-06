# M5 Petit App

## [EnglishPage](./README_en.md)

M5 Petit 用のダッシュボードサーバーです。
**N人の人間 × N体のぷち**構成に対応しています(v0.3〜)。`PETIT_DATA_DIR/characters/` 配下にキャラクターのディレクトリを1つ作るだけでキャラクターが増え(再起動不要。M5デバイス連携のみ起動時に固定)、`users.json` に人間のアカウントを増やすだけで家族それぞれのログインが増えます。ありさんの家(2人×3ぷち)のような構成がそのまま動く一般形です。

ブラウザから開くWeb UIで、写真アルバム・ボイスメモ・ノート・メールボックス・会話・記録・日記をやり取りできます。複数のぷちがいる場合は画面上部にキャラクタータブが表示され、切り替えて使えます(1体だけの場合はタブ非表示。ログインユーザーに見えるのは、そのユーザーに許可されたキャラクターだけです)。

**認証**: 初回アクセス時はセットアップページで最初のユーザー(全キャラクターへのアクセス権を持つ管理者相当)を作成し、以降はログインが必要です(cookieセッション)。2人目以降のユーザーは `scripts/add_user.py` で追加します(下記参照)。

## 機能

- 📷 **アルバム** — 写真のアップロード・一覧表示・既読/ロック管理(自動リサイズ・JPEG圧縮つき)
- 🎙️ **ボイスメモ** — 音声ファイルのアップロード・一覧表示・再生管理
- 📔 **ノート** — テキストノートの読み書き
- ✉️ **メールボックス** — メッセージの送受信・既読管理
- 💬 **会話** — Web UIから直接Claude(キャラクター)とテキストチャット。M5のマイク/カメラ/センサー起動時の自動応答もここに記録される
- 📜 **記録** — 会話ごとのClaude CLIとのやり取り(発言・思考・ツール呼び出し)を生ログとして閲覧
- 📖 **日記** — その日の会話ログをもとに、キャラクター視点の日記を生成・閲覧(手動生成ボタンつき)

## セットアップ

uvが未インストールの場合は先にインストールします。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

その後、依存関係のインストールと起動:

```bash
uv sync
uv run m5-petit-app
# もしくは
uv run uvicorn main:app --port 8765
```

デフォルトでは `http://localhost:8765` で起動します。会話・日記の生成には [Claude Code](https://docs.claude.com/claude-code) CLI(`claude`コマンド)が必要です。

初回アクセス時は `users.json` がまだ無いので、セットアップページ(`/setup`)が自動で表示されます。ここで最初のユーザー(ID・表示名・パスワード・色)を作成すると、そのユーザーは全キャラクターへのアクセス権を持つ管理者相当になります。作成後は `/login` からログインしてください。

## ユーザーの追加(2人目以降)

2人目以降の家族のアカウントは `scripts/add_user.py` で追加します(ブラウザからの追加UIはまだありません)。

```bash
# 全キャラクターにアクセスできるユーザーを追加(パスワードは対話入力)
python3 scripts/add_user.py bob "ボブ"

# 特定のキャラクターだけに限定したいとき
python3 scripts/add_user.py bob "ボブ" --characters petit_a,petit_b --color "#7da8f5"
```

そのユーザーがログインすると、`characters` に含まれるキャラクターのタブだけが表示されます。含まれないキャラクターのAPIは(存在しないキャラクターと同様に)404を返します。

## キャラクターの追加

`PETIT_DATA_DIR/characters/<character_id>/config/config.json` を1つ作るとキャラクターが増えます(サーバー再起動不要。M5デバイス連携だけは起動時に固定されるので、そこだけ再起動が必要です)。`config.json` が無いディレクトリでもディレクトリ名をIDとして扱うので、最低限ディレクトリを1つ作るだけでも動きます。

```json
{
  "id": "petit_a",
  "name": "ぷちA",
  "color": "#fff262",
  "m5_hosts": "192.168.1.50",
  "in_group": true
}
```

| フィールド | 説明 | デフォルト |
| --- | --- | --- |
| `id` | キャラクターID(省略時はディレクトリ名) | ディレクトリ名 |
| `name` | 表示名(タブ・ヘッダーで使用) | `id`と同じ |
| `color` | キャラクタータブの色(CSS color) | `#4a7c59` |
| `m5_hosts` | M5デバイスのホスト名/IP(カンマ区切り文字列、または配列) | なし |
| `in_group` | 予約フィールド(Phase C: グループ会話参加フラグ) | `true` |

## 認証の仕組み

- ユーザーは `PETIT_DATA_DIR/users.json` に保存されます(id/表示名/色/パスワードハッシュ/アクセス可能キャラクター)。パスワードは平文保存せず、標準ライブラリの `hashlib.scrypt` でハッシュ化します。
- ログインするとcookie(`petit_session`)が発行されます。中身はユーザーIDと有効期限をHMAC-SHA256で署名しただけのもの(暗号化はしていません — 秘密情報は入っていないので)。署名鍵は `PETIT_DATA_DIR/.session_secret` に初回起動時に自動生成され、`0600`権限で保存されます。
- cookieは `HttpOnly` + `SameSite=Lax`。家庭内LAN前提の軽い実装で、外部公開する場合はTailscale等の上位層に任せる想定です(HTTPS終端やIP制限はこのアプリの範囲外)。
- ユーザーの `characters` フィールドが `"all"` ならすべてのキャラクターに、配列ならそこに列挙したキャラクターIDだけにアクセスできます。含まれないキャラクターへのアクセスは、存在しないキャラクターと同じ404になります(「IDが違う」のか「権限がない」のか外から区別できないようにするため)。

## 環境変数

| 変数名 | 説明 | デフォルト |
| --- | --- | --- |
| `VOICE_API_HOST` | 音声認識(ASR)サーバーのホスト(マイク文字起こし用) | — |
| `PETIT_DATA_DIR` | データ保存先ディレクトリ | `~/petit_data` |
| `PROJECT_DIR` | Claude CLI・スクリプトのプロジェクトルート | このファイルの親ディレクトリ |
| `PORT` | 待受ポート | `8765` |

`USER_ID` 環境変数は廃止されました(v0.2系互換なし)。人間側のIDは `users.json` のアカウントに置き換わりました。`CHARACTER_ID` / `CHARACTER_NAME` / `M5_HOST` / `M5_HOSTS` 環境変数も廃止済みです(v0.1系互換なし)。キャラクターごとの設定は `config.json` に移してください。旧配置からの移行は下記の移行スクリプトを使ってください。

データは `PETIT_DATA_DIR` 配下に以下のように保存されます。`petit_data/` は他のM5 Petitツール(MCPサーバーなど)とも共有するディレクトリなので、m5-petit-appが実際に読み書きしないパスも構成として記載しています(該当行に注記)。

```
petit_data/
├── users.json                             # ★人間のアカウント(id/name/color/password_hash/characters)
├── .session_secret                        # ★cookie署名鍵(自動生成、0600)
├── resources/petit.png                    # キャラクター画像など(他ツール用、任意)
├── mailbox/                               # メールボックス(from_X_to_Y命名、既にN×N対応)
├── notebook/notebook.json                 # ノート(家族共有、キャラクター非依存)
└── characters/<character_id>/
    ├── SOUL.md                            # キャラクターの人格定義(任意、なければデフォルト文言)
    ├── album/<person_id>/                 # アルバム(v0.2〜: キャラクター配下)
    ├── voice_memo/<person_id>/            # ボイスメモ(v0.2〜: キャラクター配下)
    ├── chat_histories/<user_id>.json      # 会話ログ(v0.3〜: キャラクター×ユーザー別)
    ├── state/.session-id.<user_id>        # ★Claude CLIの--resume用セッションID(キャラクター×ユーザー別。M5ボタン操作は`.session-id._m5`)
    ├── stream_logs/*.jsonl                # 記録(Claude CLIとのやり取りの生ログ)
    ├── diary/YYYY-MM-DD.txt               # 日記(日付ごとにキャッシュ。その日そのキャラクターと話した全ユーザー分を集約)
    ├── config/
    │   ├── config.json                    # ★キャラクター設定(id/name/color/m5_hosts/in_group)。m5-petit-appが読む
    │   ├── autonomous-mcp.json            # MCPサーバー設定(任意。なければ PROJECT_DIR/autonomous-mcp.json)
    │   ├── settings.json                  # (他ツール用、m5-petit-appは未使用)
    │   └── voice_settings.json            # (他ツール用、m5-petit-appは未使用)
    ├── notes/XXXXX.md                     # キャラクター自身のメモ(他ツール用、m5-petit-appは未使用)
    └── state/last_session.txt             # (他ツール用、m5-petit-appは未使用)
```

## 旧バージョンからの移行

`scripts/migrate_v0_layout.py` が2段階の移行をまとめて(または個別に)実行します。両方とも冪等です(すでに移行済みの部分はスキップして報告するだけ)。

```bash
# 何が起きるか確認するだけ(ファイルは動かさない)
python3 scripts/migrate_v0_layout.py --character-id <旧CHARACTER_IDの値> --default-user-id <最初に作ったユーザーID> --data-dir <PETIT_DATA_DIRのパス> --dry-run

# 実際に移行
python3 scripts/migrate_v0_layout.py --character-id <旧CHARACTER_IDの値> --default-user-id <最初に作ったユーザーID> --data-dir <PETIT_DATA_DIRのパス>
```

- **v0.1系 → v0.2系(Phase A、`--character-id`)**: `photo_album/` → `characters/<id>/album/`、`voice_memo/` → `characters/<id>/voice_memo/` に移動します。続けて `characters/<id>/config/config.json`(`{"id": "<id>", "name": "..."}`)を手で作成してください。`--character-id` を省略するとこの段階はスキップされます。
- **v0.2系 → v0.3系(Phase B、`--default-user-id`)**: すべてのキャラクターについて `chat_histories/chat_history.json`(1本だった会話ログ)を `chat_histories/<default-user-id>.json` に振り分けます(渡したユーザーIDが今後の「唯一の会話相手」として扱われる、という意味です)。セットアップページで最初のユーザーを作ってからこのIDを渡すのが自然です。`--default-user-id` を省略するとこの段階はスキップされます。

## スクリプト

- `scripts/write_mailbox.py` — コマンドラインからメールボックスにメッセージを書き込むツール。

  ```bash
  python3 scripts/write_mailbox.py <from_id> <to_id> <content>
  python3 scripts/write_mailbox.py <from_id> <to_id> --file <path>
  ```

- `scripts/add_user.py` — `users.json` にユーザーを追加するツール(上記「ユーザーの追加」参照)。
- `scripts/migrate_v0_layout.py` — 旧バージョンのデータ配置を移行するツール(上記参照)。

## テスト

```bash
uv run ruff check .
uv run pytest -v
```
