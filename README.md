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
| 世界の生成・取得 | `python run.py world --help` / [詳細](docs/WORLD_GENERATION.md) |
| GLBの準備 | `python run.py prepare "対象.glb"` / [詳細](docs/SKY_CLEANUP.md) |
| 窓・器具の位置記録 | `python run.py map --help` / [詳細](docs/LIGHT_PLACEMENT.md) |
| 太陽・空の設定関数 | [使い方](docs/SUN_SKY.md) |
| 小物画像生成 | `python run.py images --help` / [詳細](docs/IMAGE_GENERATION.md) |
| 入力フォルダ監視 | `python run.py trellis --help` / [詳細](docs/TRELLIS_FOLDER.md) |
| HPC操作 | `python run.py hpc --help` / [詳細](docs/TRELLIS_HPC.md) |

配置記録の入口は従来どおり位置と種類を記録します。太陽・空の関数は明示的に呼んだときに適用します。

## 設定とGit

- `.env.example` を参考に、APIキーをルートの `.env` に設定します。
- 世界の説明文は `config/world_prompt.txt`、空の初期設定は `config/sun_sky.json` です。
- 個人環境の設定は `config/trellis.local.json` です。新しい環境では `config/trellis.example.json` をコピーして作成します。ローカル設定がないときは設定例を読みます。
- `.env`、`*.local.json`、`outputs/`、`trellis_input/` の入力画像、`scratch/` はGit対象外です。
- 既存の `outputs/` と入力画像は移動せず保持しています。保存済みの実行記録からの参照も維持します。

整理前のソースは `scratch/reorganization_backup/` にあります。設定の控えも含むため、`scratch/` 全体をGit対象外にしています。

Blenderから本体をimportするときは `src` をPythonの検索先へ追加します。検証スクリプトの実行方法は [tests/README.md](tests/README.md) を参照してください。
