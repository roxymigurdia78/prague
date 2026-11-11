# Prague

Marbleで作った街並みをBlenderで準備し、HPCのVLMで窓・街灯・ランタンの位置を記録する研究用プロジェクトです。太陽・空の9項目を更新する関数も含みます。

## フォルダの役割

| 場所 | 内容 | 削除してよいか |
| --- | --- | --- |
| `src/streetlab/` | Pythonの本体・Blender用処理 | 保持 |
| `config/` | 生成文・空の初期値・設定例 | 保持 |
| `tests/` | 自動テストのコード | 保持 |
| `docs/` | 使い方・研究メモ | 保持 |
| `scripts/windows/` | ダブルクリック・ドラッグ用の起動ファイル | 保持 |
| `scratch/` | 試し書き・一時画像・テスト結果・整理前の控え | **中身ごと削除してよい** |
| `outputs/` | 生成済みの世界・処理結果・実行記録 | 必要な成果物を保持 |
| `trellis_input/` | TRELLISに渡す元画像 | 必要な入力を保持 |

`scratch/` はGit対象外です。自由な実験はここで行ってください。`tests/` は機能を確認するコードなので、実験のゴミ箱とは分けています。

## 実行

このフォルダで実行します。既存のPython環境ならインストールなしで入口を使えます。

日本語の会話から始める場合は、ルートの `会話.json` と `.env` のWorldキーを編集し、`会話から世界を作る.cmd` をダブルクリックします。コマンドは `python run.py conversation-build` です。既存の会話抽出を使ってHPCが英語プロンプト・地点・日時を作り、その条件を一括処理へ引き継ぎます。[会話から作るメモ](会話から作るメモ.txt) に記入例と再開方法があります。同じ会話の再実行は保存済みの世界を再利用します。

普段は `.env` の `WORLD_LABS_API_KEY` と `config/world_prompt.txt` を編集し、ルートの `世界を作る.cmd` をダブルクリックします。`python run.py build` でも同じ処理です。世界生成・HQ GLB・清掃・消灯・地名/標高/大気・窓/街灯/ランタンの3D検出・太陽と空を設定した初期Blender・カメラ位置の土台まで自動実行します。本撮影は既定では行いません。コピーして使えるコマンドは [実行メモ.txt](実行メモ.txt) と [詳しいコマンド一覧](docs/COMMANDS_JA.md) にまとめています。

```powershell
python run.py --help
python run.py test
python run.py world generate --dry-run
```

新しい環境を用意する場合はPython 3.10以上で次を実行します。Blenderは別途必要です。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python run.py test
```

| 処理 | コマンド・説明 |
| --- | --- |
| Windowsの会話JSONから初期Blenderまで一括 | `python run.py conversation-build` / [会話用メモ](会話から作るメモ.txt) |
| プロンプトから3D検出・初期Blenderまで一括 | `python run.py build` / [実行メモ](実行メモ.txt) |
| 既存の街へ道路探索・本撮影を追加 | `python run.py build --resume --with-capture --run-dir "対象の保存先"` |
| Macの会話JSON受信・世界生成 | `python run.py conversation --help` / [送信形式・受信先・データの流れ](docs/CONVERSATION_INPUT.md) |
| 世界の生成・取得 | `python run.py world --help` / [詳細](docs/WORLD_GENERATION.md) |
| GLBの準備 | `python run.py prepare "対象.glb"` / [詳細](docs/SKY_CLEANUP.md) |
| 道路認識・探索・撮影計画 | `python run.py roads --help` / [接続とコマンド](docs/COMMANDS_JA.md) |
| 窓・器具の位置記録 | `python run.py map --help` / [詳細](docs/LIGHT_PLACEMENT.md) |
| 太陽・空の設定関数 | [使い方](docs/SUN_SKY.md) |
| エアロゾル・オゾン全量の取得 | `python run.py atmosphere --help` / [CAMS EAC4](docs/ATMOSPHERE_REFERENCE.md) |
| 小物画像生成 | `python run.py images --help` / [詳細](docs/IMAGE_GENERATION.md) |
| 入力フォルダ監視 | `python run.py trellis --help` / [詳細](docs/TRELLIS_FOLDER.md) |
| HPC操作 | `python run.py hpc --help` / [詳細](docs/TRELLIS_HPC.md) |

検出結果はJSONとEMPTYマーカーへ記録し、照明配置や窓の発光は行いません。`street_initial.blend` は地点・日時・大気を反映したSkyテクスチャを持ち、`street_relight.blend` / `.glb` は自発光・環境光ゼロの素材として別に残ります。保存シーンにカメラ・ライトオブジェクトは残しません。撮影の土台は `camera.json` です。

## 設定とGit

- `.env.example` を参考に、APIキーをルートの `.env` に設定します。
- 世界の説明文は `config/world_prompt.txt`、空の初期設定は `config/sun_sky.json` です。
- 個人環境の設定は `config/trellis.local.json` です。新しい環境では `config/trellis.example.json` をコピーして作成します。ローカル設定がないときは設定例を読みます。
- `.env`、`*.local.json`、`outputs/`、`trellis_input/` の入力画像、`scratch/` はGit対象外です。
- 既存の `outputs/` と入力画像は移動せず保持しています。保存済みの実行記録からの参照も維持します。

整理前のソースは `scratch/reorganization_backup/` にあります。設定の控えも含むため、`scratch/` 全体をGit対象外にしています。

Blenderから本体をimportするときは `src` をPythonの検索先へ追加します。検証スクリプトの実行方法は [tests/README.md](tests/README.md) を参照してください。
