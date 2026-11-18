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
| `scratch/` | 試し書き・一時画像・テスト結果・整理前の控え | 通常は削除可。ここに置いたWSL版Blenderを撮影に使う間は保持 |
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
| 初期Blenderの保存後に昼光比較を追加 | `python run.py build --with-lighting-capture` または `python run.py conversation-build --with-lighting-capture` |
| 清掃済みの街から昼光比較だけ撮影 | `python run.py build --resume --with-lighting-capture --run-dir "対象の保存先"` または `python run.py lighting-capture "対象の保存先"` |
| 既存の街へ道路探索・本撮影を追加 | `python run.py build --resume --with-capture --run-dir "対象の保存先"` |
| 道路の本撮影から照明探索まで一括 | `python run.py conversation-build --with-lighting-search` または `python run.py build --with-lighting-search` |
| 本撮影済みの街から照明探索を開始・再開 | `python run.py lighting-search "対象の保存先"` |
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

## 位置の記録から撮影まで

1. **位置の記録。** 清掃済みの街をHPCのVLMで調べ、窓・街灯・ランタンの位置と向きを `inventory.json` に保存します。`street_initial.blend` には位置を示すEMPTYマーカーが入ります。実際のライトの配置や窓の発光はこの段階では行いません。
2. **初期シーン。** `street_initial.blend` は地点・日時・大気を反映した空を持ちます。撮影位置の候補は `camera.json`、初期シーンや検出結果への参照は `result.json` に保存します。`street_relight.blend` / `.glb` は自発光・環境光ゼロの素材です。
3. **昼光の比較撮影。** `--with-lighting-capture` を付けると、保存された初期シーンとカメラ位置を使い、露出・ホワイトバランス・コントラスト・太陽光・環境光・かすみを変えて撮影します。検出結果の参照と件数も設定に記録します。画像・Blenderファイル・設定値は `lighting_capture/index.html` から開けます。既存の初期シーンからの再開では、世界生成・清掃・VLMによる再検出を省きます。

複数の照明設定を同時に変える比較には `lighting-capture` の `--mixed N` を使います。道路を探索して複数の視点から撮る場合は `--with-capture` を選びます。昼光比較の実行例、時間帯の扱い、WSL版Blenderの指定は [詳しいコマンド一覧](docs/COMMANDS_JA.md#初期blenderから昼光の比較だけを撮影) にあります。

## 本番の照明ループ

空に灰色の面・黒い線・小片が残っている場合は、照明探索の前段の空清掃を再確認できます。完成済みの街には `run.py build --resume --refine-sky --run-dir "街の保存先"` を使います。最後に採用した形状から、黒い部分も拡大と周囲の文脈で精査します。既定では修正が進む間の回数上限を設けず、通常画像と元解像度の細部検査の両方で候補なしになった場合だけ完了します。建物か欠片か判定できず進めない場合は未完了として止め、後段の初期シーンや照明探索へ進みません。元の世界を残して修正版を別保存します。[空清掃の仕組み・再検査方法](docs/SKY_CLEANUP.md#空に残る線薄い面の再検査)を参照してください。

学習済みLoRAを使う場合は、[HPCのモデル登録・API公開・Windows設定](docs/LIGHTING_API.md)を参照してください。`.env` の `LIGHTING_API_URL` と `LIGHTING_MODEL=a_all` を設定すると、HPC側に登録したその名前のモデルを呼び出します。モデル名が空欄なら設定を求めて停止します。

道路の本撮影が終わった街を起点にします。`result.json` から初期Blenderと本撮影の記録を読み、実際に使った道路カメラを再現します。日時・北・太陽の向きと高さは本撮影から引き継ぎます。対象はAgXで撮影した街です。

1. 会話で作った新しいワールドごとに、その街の初期設定から最初の24案を広く試します。以降は、見た目の異なる良い案を数本残し、毎周最大12案を作ります。近くを少し動かす案、枝同士の設定を混ぜる案、範囲全体から選ぶ案を組み合わせます。最近の勝敗・枝の系統・しばらく試していない期間から配分を更新し、改善している枝に局所試行を多めに配ります。その周に試行を割り当てない枝も履歴に残します。通常は新しい領域を試す枠を少なくとも2案残します。共通地点の前後画像を縮小して比較し、見た目がほぼ同じ有力案は代表案へ整理します。
2. 同じ道路地点から前方と後方の画像を作り、Qwenに設定名を伏せて親と候補を比較させます。まず代表地点で絞り、有力案に追加の地点を使います。採用判定では画像の順番を入れ替えて確認し、道路区間ごとに票をまとめます。前後の判定が引き分けや不安定だった場合は、同じ保存済みパノラマから左右90°・270°を切り出して追加確認します。元の視点に明確な反対の選択がある場合は、追加方向だけでそれを上書きしません。3周ごとに暫定最良を含む上位最大4案を探索用の共通視点で総当たり比較し、勝ち点が同じならその時の暫定最良を維持します。枝の保持数を4本に固定する制限ではありません。
3. 既定は30分の探索です。`--minutes 120` なら120分、`--rounds 6` なら動作確認として累計6周と時間の早い方まで進めます。時間内に暫定最良が更新されなくても、最良案を残して広く探し直します。最後は探索中に使わなかった地点で初期設定と有力案を比較し、選ばれた最良案と他の有力案を保存します。最良案の最終更新が何周目か、以降何周更新されていないかも結果ページに表示します。

本番は「一度会話する → 新しい街を生成 → その街の中で光を探索 → その探索で選んだ結果を保存」という一回の流れです。別ワールドや別モデルの前回成果は自動では引き継ぎません。共通で使うのは学習済みの審査モデルです。通常の `lighting-search` は学習・新旧モデルの比較実験を起動しません。

| つまみ | 探索範囲 |
| --- | --- |
| 太陽光、環境光 | 本撮影の各0.5〜2倍 |
| かすみ | 本撮影の0.5〜3倍、Blenderの上限まで |
| 露出 | 本撮影から−2〜＋2 EV |
| ホワイトバランスの色温度、色かぶり | 4,000〜8,000 K、色かぶりは本撮影から−10〜＋10 |
| AgXのコントラスト | 弱・標準・強 |

数値のつまみは小数で動かします。太陽光・環境光・かすみなどの倍率は比率で扱い、探索範囲の中で最大小数点以下6桁まで候補を作ります。コントラストだけはBlenderのAgXの3種類から選びます。

候補の作り方は `adaptive-gaussian-v1` です。追加地点と画像順の入れ替えで確認した比較結果から、良かった変更方向、複数のつまみを一緒に動かした関係、枝ごとの成功率を使って、次の候補の方向と動かす幅を更新します。たとえば環境光を上げながら露出を下げた変更が良ければ、その組み合わせを次の候補に反映します。改善しない変更が続くと細かく探し、さらに停滞すると幅を広げます。離れた枝の成功例も弱く参考にします。探索範囲と停止条件は引き続き設けており、人から見たリアルさへの収束を保証するものではありません。

各候補の `distribution` に、その時点の探索幅と学習したつまみ同士の関係を記録します。これまでの保存先から再開すると、過去の比較履歴を使って次の周からこの方式で候補を作ります。中断した周の `round-XXX.json` がある場合は、その候補を最後まで使ってから切り替えます。最後の確認地点の評価は候補生成には使いません。

### 窓・街灯・ランタンの配置

夕方・明け方・夜は、一括実行が保存した `inventory.json` の確定済みの位置と法線を読みます。器具や窓のどの組を点灯するか、光量、色、壁面からの小さな位置調整を候補に加えます。候補ごとに街全体の配置を一度作り、全道路カメラで共有します。夜は本撮影の太陽位置と消灯状態を基準として、候補に弱い夜空の環境光と人工光を加えます。昼に配置も試す場合は `lighting-search` に `--placement on` を付けます。

以前の配置ループの結果から続ける場合は、`lighting-search` に `--placement-run "outputs/light_placement/対象の保存先"` を付けます。同じ元のBlenderを使った実行が対象です。最後の配置計画で点灯になった灯具と調整後の光量を引き継ぎ、その組を最初の比較候補にも入れます。日時と太陽位置は道路の本撮影に合わせます。

### 実行と再開

会話から道路撮影と照明探索まで続けます。既存のHPC接続設定を使います。

```powershell
.\.venv\Scripts\python.exe -X utf8 run.py conversation-build --with-lighting-search
```

これで、その会話から作ったワールドを対象に光を30分探索します。一括コマンドで時間を変える場合は `--lighting-minutes 120` を追加します。`run.py build --with-lighting-search` でも同じ指定が使えます。30分や120分は光の探索部分の時間で、前段の世界生成・空清掃・道路撮影と、最後の比較・Blender保存には別途時間がかかります。

```powershell
.\.venv\Scripts\python.exe -X utf8 run.py conversation-build --with-lighting-search --lighting-minutes 120
```

道路の本撮影が済んだ街では、下のコマンドだけで開始できます。実行するたびに、モデル名と開始日時（日本時間）のフォルダを自動で作ります。例は `lighting_search/a_all_20261117_163000/` です。各回の画像・判定・枝の履歴・最終比較を分けて保存します。

```powershell
.\.venv\Scripts\python.exe -X utf8 run.py lighting-search "outputs/builds/対象の保存先"
```

`--output` は実行フォルダを置く親フォルダです。省略した場合は街の `lighting_search/`、配置も探索する場合は `lighting_search_placement/` の中に保存します。端末には今回の保存先を表示します。親フォルダの `lighting_history.html` から過去の結果を一覧で開けます。

通常の起動は毎回新規探索です。同じワールド・同じモデルの実行を中断したときに限り、必要なら `--resume "中断した実行フォルダ"` で明示的に続けられます。この場合は保存済みの画像・VLMの判定・枝の履歴・中断した周を新しい日時の保存先へ引き継ぎ、元のフォルダを保持します。`--output` に `state.json` がある旧実行フォルダを渡した場合は、その親の中に日時付きの新しい実行を作りますが、履歴は自動では引き継ぎません。

`--dry-run` は保存先の例・撮影地点・時間設定を表示するだけで、API接続やフォルダ作成はしません。時間指定がなければ30分です。短く試す場合は `--rounds 1`、同じ中断実行を続ける場合は `--resume` を付けます。初期シーンまでの街から続ける場合は `run.py build --resume --with-lighting-search --run-dir "対象の保存先"` で道路の本撮影からつなげます。

時間を決めて長く試す場合は `--minutes 120` を指定します。今回の起動から120分を探索に使い、改善なしで停止済みの保存先からも続けられます。停滞時は最良案と枝を残して範囲全体を探し直します。`--rounds` を併記した場合は累計周回上限と時間の早い方で探索を終えます。指定時間に達すると新しい撮影・比較を始めず、途中の周は保存済みの候補・画像・判定から次回再開します。実行中の撮影やAPI要求の終了を待つ時間と、最後の比較・Blender保存の時間は追加でかかります。

時間指定で再開したときの最良案は `retained_finalists` に残し、最後に新しい有力案とも比較します。

```powershell
.\.venv\Scripts\python.exe -X utf8 run.py lighting-search "outputs/builds/20261109_234836_223354" --output "outputs/lighting_trials" --minutes 120
```

### 最後に保存する世界の表示

最終比較に載せるBlenderファイルも `モデル名_日時_候補ID.blend` で保存します。カメラと検出用のEmptyマーカーは各ビューレイヤーの目の状態を非表示にします。ライトは光を出す状態を保ち、ビューポートの `Extras` と関連線の表示を切って枠線を隠します。カメラ枠のない道路視点で開くように保存します。形状、撮影用カメラ、照明オブジェクトは保持します。初期照明の比較ファイルにも同じ表示設定を適用します。

補助表示を戻す場合は、アウトライナーで対象の目を有効にし、ビューポートのオーバーレイで `Extras` を表示します。この表示整理は最後の保存時に行います。既に保存済みの過去のBlenderファイルは書き換えません。

### 新しいノートでA_allを低い学習率で追加学習する

新しいノートはHPCの `~/light_eval/aachen_a_all_low_lr.ipynb` に保存しています。ローカルには置きません。HPCで開き、`light_eval (Py3.12)` カーネルで①〜④を実行します。既存のノートは変更しません。保存済みの `A_all` から学習率 `1e-5`（元の `1e-4` の10分の1）で追加1エポックを学習します。元と同じ採点・写真単位の分割・入力条件をGPU読込前に確認します。元の最適化状態は引き継がず、低い学習率の別実験として開始します。

保存先は `~/light_eval/outputs/aachen_a_all_low_lr_v1/A_all/adapter` です。途中再開はカーネルを再起動し、同じ設定で①から実行します。この実験の完全なチェックポイントだけを再開に使います。別の条件で試す場合は①の `OUTPUT` も変更してください。⑤の比較評価、API登録・モデル切替は含みません。学習終了後はカーネルを終了するとGPUメモリを解放できます。他のカーネルやAPIは自動停止しません。同じ採点データの追加学習なので、性能が改善したかは後の評価が必要です。

GB10ではCPUとGPUがメモリを共有するため、CUDAの空き容量だけでは再利用可能なOSキャッシュを含む利用可能量を把握できません。新しいノートはGB10の場合に `/proc/meminfo` の `MemAvailable` で開始前確認を行い、他のGPUではCUDAの空き容量を使います。PyTorchのメモリ使用上限は64 GiBのままです。キャッシュの強制削除は行いません。[NVIDIAの共有メモリに関する説明](https://docs.nvidia.com/dgx/dgx-spark/known-issues.html)

### 探索のあとにHPCで追加学習する

`scripts/research/lighting_study_session.py` は、このウィーンの探索を時間指定で再開したあと、HPCのGPUを追加学習へ順番に渡します。以下の準備と起動は各セッションにつき一度です。準備では、HPCの元ノートを実験先に写し、採点CSV・2,500学習ペア・元写真単位の225/25組の分割・質問が元の `A_all` と一致するか確認します。元ノートと元のLoRAは変更しません。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/research/lighting_study_session.py --session "outputs/lighting_sessions/lighting_study_20261117_01" --minutes 120 --prepare
.\.venv\Scripts\python.exe -X utf8 scripts/research/lighting_study_session.py --session "outputs/lighting_sessions/lighting_study_20261117_01" --run
```

探索が最終比較まで成功すると、HPCの比較サービスを一時停止し、`A_all` の保存済み重みから追加1エポックを学習率 `1e-5` で学習します。同じ採点データの繰り返しであり、新しい人の採点を増やす処理は含みません。学習先は別の `outputs/lighting_study_.../experiment/A_all` です。新しい実験の完全なチェックポイントから学習を再開できます。元の2エポックの最適化状態は引き継がず、学習率を下げた別実験として最適化を始めます。[PEFTの学習可能な重みの読み込み](https://huggingface.co/docs/peft/package_reference/peft_model)

学習後、元モデルと追加学習モデルを、同じ実写真確認用375ペアとウィーン227ペアで左右順を入れ替えて比較します。確認用データは追加学習に入れません。ウィーンは以前から結果を見ているため、この再評価は探索的な比較です。CSVはHPCの実験先の `experiment/pair_eval_summary.csv` に保存します。新モデルを別名でAPIへ追加し、比較サービスを元のポートで復帰させます。Windowsの `.env` と既存のモデル選択は変更しません。途中で学習・評価に失敗した場合もサービスを復帰させる処理が入っています。HPCを強制終了した場合は [API起動手順](docs/LIGHTING_API.md)で復帰してください。

進捗はローカルのセッション内 `status.json`、HPCの同名実験先の `status.json` と `training.log` に残ります。次で両方を確認できます。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/research/lighting_study_session.py --session "outputs/lighting_sessions/lighting_study_20261117_01" --status
```

起動済みの学習を自動で二重起動しないようにしています。SSH応答が途切れた場合は、まず `--status` とHPCの `launch.json` を確認します。学習の途中再開は、HPCの `.venv312/bin/python` で `lighting_jobs/continue_lighting_training.py --output "その実験先" --model-name "そのモデル名"` を、`experiment_config.json` と同じ条件で実行します。

結果は今回の実行フォルダの `index.html`、過去との一覧は親フォルダの `lighting_history.html` で見られます。`run.json` にモデル名・日時・再開元、`summary.json` に最終比較・つまみ・灯具IDと配置、`state.json` に分岐と親子関係、`comparisons/` に地点ごとのVLM判定と理由、`finalists/` に候補のBlenderファイルを保存します。推奨はVLMによる判定です。研究で人から見た改善を測る場合は、同じ確認地点の画像を設定名を伏せて比較します。

## 設定とGit

- `.env.example` を参考に、APIキーをルートの `.env` に設定します。
- 世界の説明文は `config/world_prompt.txt`、空の初期設定は `config/sun_sky.json` です。
- 個人環境の設定は `config/trellis.local.json` です。新しい環境では `config/trellis.example.json` をコピーして作成します。ローカル設定がないときは設定例を読みます。
- `.env`、`*.local.json`、`outputs/`、`trellis_input/` の入力画像、`scratch/` はGit対象外です。
- 既存の `outputs/` と入力画像は移動せず保持しています。保存済みの実行記録からの参照も維持します。

整理前のソースは `scratch/reorganization_backup/` にあります。設定の控えも含むため、`scratch/` 全体をGit対象外にしています。

Blenderから本体をimportするときは `src` をPythonの検索先へ追加します。検証スクリプトの実行方法は [tests/README.md](tests/README.md) を参照してください。
