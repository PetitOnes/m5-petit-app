# M5 Petit App

## [EnglishPage](./README_en.md)

M5 Petit 用のダッシュボードサーバーです。
**1ユーザー × N体のぷち**構成に対応しています(v0.2〜)。`PETIT_DATA_DIR/characters/` 配下にキャラクターのディレクトリを1つ作るだけでキャラクターが増え、再起動なしでWeb UIのタブに反映されます(M5デバイス連携のみ起動時に固定)。
※ 複数ユーザー(家族それぞれのログイン)対応はまだ未実装です。今は1人のユーザーが複数のぷちと話す形で、認証・ユーザーごとの会話履歴分離は今後のフェーズで追加予定です。

ブラウザから開くWeb UIで、写真アルバム・ボイスメモ・ノート・メールボックス・会話・記録・日記をやり取りできます。複数のぷちがいる場合は画面上部にキャラクタータブが表示され、切り替えて使えます(1体だけの場合はタブ非表示)。

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

## 環境変数

| 変数名 | 説明 | デフォルト |
| --- | --- | --- |
| `USER_ID` | 人間側のユーザーID | `user` |
| `VOICE_API_HOST` | 音声認識(ASR)サーバーのホスト(マイク文字起こし用) | — |
| `PETIT_DATA_DIR` | データ保存先ディレクトリ | `~/petit_data` |
| `PROJECT_DIR` | Claude CLI・スクリプトのプロジェクトルート | このファイルの親ディレクトリ |
| `PORT` | 待受ポート | `8765` |

`CHARACTER_ID` / `CHARACTER_NAME` / `M5_HOST` / `M5_HOSTS` 環境変数は廃止されました(v0.1系互換なし)。キャラクターごとの設定は上記の `config.json` に移してください。旧配置からの移行は下記の移行スクリプトを使ってください。

データは `PETIT_DATA_DIR` 配下に以下のように保存されます。`petit_data/` は他のM5 Petitツール(MCPサーバーなど)とも共有するディレクトリなので、m5-petit-appが実際に読み書きしないパスも構成として記載しています(該当行に注記)。

```
petit_data/
├── resources/petit.png                   # キャラクター画像など(他ツール用、任意)
├── mailbox/                              # メールボックス(from_X_to_Y命名、既にN×N対応)
├── notebook/<user_id>/notebook.json      # ノート(共有、キャラクター非依存)
└── characters/<character_id>/
    ├── SOUL.md                           # キャラクターの人格定義(任意、なければデフォルト文言)
    ├── album/<person_id>/                # アルバム(v0.2〜: キャラクター配下に移動)
    ├── voice_memo/<person_id>/           # ボイスメモ(v0.2〜: キャラクター配下に移動)
    ├── chat_histories/chat_history.json  # 会話ログ
    ├── stream_logs/*.jsonl               # 記録(Claude CLIとのやり取りの生ログ)
    ├── diary/YYYY-MM-DD.txt              # 日記(日付ごとにキャッシュ)
    ├── config/
    │   ├── config.json                   # ★キャラクター設定(id/name/color/m5_hosts/in_group)。m5-petit-appが読む
    │   ├── autonomous-mcp.json           # MCPサーバー設定(任意。なければ PROJECT_DIR/autonomous-mcp.json)
    │   ├── settings.json                 # (他ツール用、m5-petit-appは未使用)
    │   └── voice_settings.json           # (他ツール用、m5-petit-appは未使用)
    ├── notes/XXXXX.md                    # キャラクター自身のメモ(他ツール用、m5-petit-appは未使用)
    └── state/last_session.txt            # セッション再開用(他ツール用、m5-petit-appは未使用)
```

## v0.1系からの移行

v0.1系(`CHARACTER_ID`環境変数固定・`photo_album/`/`voice_memo/`が共有ディレクトリ直下)からv0.2系へは `scripts/migrate_v0_layout.py` で移行します。

```bash
# 何が起きるか確認するだけ(ファイルは動かさない)
python3 scripts/migrate_v0_layout.py --character-id <旧CHARACTER_IDの値> --data-dir <PETIT_DATA_DIRのパス> --dry-run

# 実際に移動
python3 scripts/migrate_v0_layout.py --character-id <旧CHARACTER_IDの値> --data-dir <PETIT_DATA_DIRのパス>
```

`--character-id` を省略すると `CHARACTER_ID` 環境変数を使います。`photo_album/` → `characters/<id>/album/`、`voice_memo/` → `characters/<id>/voice_memo/` に移動し、続けて `characters/<id>/config/config.json` を作成してください(移行スクリプトは設定ファイルまでは作らないので、`{"id": "<id>", "name": "..."}` を手で追加します)。`chat_histories/` `stream_logs/` `diary/` はv0.1系でも既にキャラクター配下だったので変更ありません。

## スクリプト

- `scripts/write_mailbox.py` — コマンドラインからメールボックスにメッセージを書き込むツール。

  ```bash
  python3 scripts/write_mailbox.py <from_id> <to_id> <content>
  python3 scripts/write_mailbox.py <from_id> <to_id> --file <path>
  ```

- `scripts/migrate_v0_layout.py` — v0.1系のデータ配置をv0.2系に移行するツール(上記参照)。

## テスト

```bash
uv run ruff check .
uv run pytest -v
```
