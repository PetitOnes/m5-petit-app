# M5 Petit App

M5 Petit 用のダッシュボードサーバーです。
1ユーザー / 1台のM5構成を前提とした、シンプルな常時起動サーバーとして動かせます。

ブラウザから開くWeb UIで、写真アルバム・ボイスメモ・ノート・メールボックス・会話・記録・日記をやり取りできます。

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

## 環境変数

| 変数名 | 説明 | デフォルト |
| --- | --- | --- |
| `CHARACTER_ID` | M5キャラクターID | `petit` |
| `USER_ID` | 人間側のユーザーID | `user` |
| `M5_HOST` / `M5_HOSTS` | M5デバイスのホスト名/IP(カンマ区切りでフォールバック指定可) | — |
| `VOICE_API_HOST` | 音声認識(ASR)サーバーのホスト(マイク文字起こし用) | — |
| `PETIT_DATA_DIR` | データ保存先ディレクトリ | `~/petit_data` |
| `PROJECT_DIR` | Claude CLI・スクリプトのプロジェクトルート | このファイルの親ディレクトリ |
| `PORT` | 待受ポート | `8765` |

データは `PETIT_DATA_DIR` 配下に以下のように保存されます(このアプリ自身が読み書きするものだけを記載。memory-mcp等の他ツールが使う設定/データファイルは含みません)。

```
petit_data/
├── mailbox/                              # メールボックス
├── photo_album/<character_id|user_id>/   # アルバム
├── voice_memo/<character_id|user_id>/    # ボイスメモ
├── notes/<user_id>/notebook.json         # ノート
└── characters/<character_id>/
    ├── SOUL.md                           # キャラクターの人格定義(任意)
    ├── config/mcp.json                   # MCPサーバー設定(任意)
    ├── chat_histories/chat_history.json  # 会話ログ
    ├── stream_logs/*.jsonl               # 記録(Claude CLIとのやり取りの生ログ)
    └── diary/YYYY-MM-DD.txt              # 日記(日付ごとにキャッシュ)
```

## スクリプト

`scripts/write_mailbox.py` — コマンドラインからメールボックスにメッセージを書き込むツール。

```bash
python3 scripts/write_mailbox.py <from_id> <to_id> <content>
python3 scripts/write_mailbox.py <from_id> <to_id> --file <path>
```
