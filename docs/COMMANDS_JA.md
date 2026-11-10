# 現在のコマンドメモ（2026-11-10）

現在のプロジェクトは `C:\Users\Astra\Desktop\prague`。旧メモの `test1` と `python world.py ...` は、現在は `prague` と `python run.py world ...` に読み替えます。

メモ帳で読むための短い一覧は、ルートの [実行メモ.txt](../実行メモ.txt) です。

日本語の会話をWindows上のファイルへ書く場合は、`会話.json` を編集して `python run.py conversation-build` または `会話から世界を作る.cmd` を実行します。HPCがプロンプト・地点・日時を生成し、初期Blenderまで接続します。詳しい手順は [会話から作るメモ](../会話から作るメモ.txt) です。

## 普段使う一括コマンド

`config/world_prompt.txt` を編集して保存したら、次だけで実行できます。

```powershell
cd C:\Users\Astra\Desktop\prague
.\.venv\Scripts\python.exe -X utf8 run.py build
```

ルートの `世界を作る.cmd` のダブルクリックでも同じ処理を実行します。新しい街では、先に `.env` の `WORLD_LABS_API_KEY` と生成プロンプトを変更します。HPC/CAMSの既存設定は引き継ぎます。

現在の既定は初期状態までで、道路探索と本撮影を省きます。GLBの準備・地名/標高/気象/大気・窓/街灯/ランタンの3D検出・初期Blender保存まで進めます。`--initialize-only` を明示しても同じです。清掃・検出に必要な内部画像は作り、カメラ位置の土台は `camera.json` に保存します。後日、同じ街へ `python run.py build --resume --with-capture --run-dir "その街の保存先"` で道路探索と本撮影を追加できます。

正常なGLBがローカルに保存済みなら、後処理はWorld APIへの再照会を省きます。キー変更後も元の世界を生成し直さずに再開できます。
GLB取得前に停止したジョブを再開するときは、その街を生成したWorldキーが必要です。

既存のHPC抽出・地名検索・日付/時刻選択・気象取得・CAMS取得・Marble生成・HQ GLB書き出し・`prepare`・`scene_inventory`・太陽と空の設定を順に呼びます。地名をプロンプトから抽出し、季節/時間帯または明示日時がそろっていれば既存の日付・時刻選択を呼びます。足りない場合は `config/build_world.json` の初期値（2026-07-15の現地12時）を使います。CAMS・気象の参照年の既定は2025年で、リアルタイムの天候ではありません。`--date` と `--time` の明示指定を優先します。

`build` は入力された生成プロンプトをそのまま保存・送信し、HPCには地名・日時などの条件だけを抽出させます。会話から生成用の場所本文を書き直す `conversation` の厳格な本文検査は、この条件抽出には適用しません。地名・日時・抽出根拠の検証は引き続き行います。

`street_relight.blend` / `.glb` は消灯済みの素材、`street_initial.blend` は大気と太陽を設定済みの初期シーンです。太陽はSkyテクスチャで表現し、Sunライトオブジェクトは置きません。検出JSONを `inventory/` に記録し、初期シーンにはEMPTYマーカーを読み込みます。窓の発光や街灯ライトの配置は行いません。保存するシーンのカメラ・ライトオブジェクトは0です。全体の記録は `outputs/builds/日時/` の `result.json` から参照できます。本撮影を明示した場合だけ画像と設定を `renders/shot-.../settings.json` に記録します。

初期シーンと試写は `AgX`、露出 `-2 EV` を使います。照明の物理設定を保ったまま、確認用のStandard色変換を引き継いで白飛びすることを防ぎます。`config/build_world.json` の `view_transform` と `exposure` で変更でき、使用値は各試写の設定JSONに記録します。

```powershell
# 地名の抽出を省き、指定地点で実行
.\.venv\Scripts\python.exe -X utf8 run.py build --place Prague --country CZ

# 日時を指定
.\.venv\Scripts\python.exe -X utf8 run.py build --date 2026-11-09 --time 15:00

# APIなしで設定だけ確認
.\.venv\Scripts\python.exe -X utf8 run.py build --dry-run

# 保存済み条件・ジョブから再開（新しい世界を生成しない）
.\.venv\Scripts\python.exe -X utf8 run.py build --resume
```

通常の `build` は、最新の未完了ジョブとプロンプトが同じなら再開します。プロンプトが変わった場合や完了済みの場合は、新しい保存先で新規生成します。`--resume` では保存済みプロンプトを使います。過去の成果物は残ります。`--run-dir` で一括実行の保存先、`--world-dir` で既に生成済みの世界の保存先を指定できます。既存世界を指定すると、生成記録にある元のプロンプトとモデルを引き継ぎます。Worldキーはプロセス起動時の値に固定するため、実行中に次の街用のキーへ `.env` を変更しても、進行中の生成とGLB取得には同じキーを使います。

新規生成前には、その時点で残っている世界生成・GLB書き出し両方の料金を賄えるか確認します。既存の送信記録と送信結果不明マーカーは再利用し、不明なPOSTを自動再送しません。同じ一括実行の並列起動もロックします。

道路と本撮影は `--with-capture` のときに実行します。旧 `test1/scratch/mask2former-transfer` の6本を `src/streetlab/roads` へ移し、HPCのMask2Formerで道路・歩道・歩行者エリアを認識、HPC VLMで分岐探索、Blenderで連結した歩行可能なグリッドと撮影計画を作成します。その計画を既存の `capture_world_blender.install_cameras()` と `render_views()` へ渡し、大気付きの道路視点画像と各カメラの位置・向き・露出・空の実際の設定を保存します。初期値は2ラウンド、最大12パノラマで、`config/build_world.json` の `roads` 欄に固定します。

窓・街灯・ランタンの3D検出は既定で有効です。`scene_inventory` が2ラウンド、各地点8方向を解析し、位置・向き・候補領域をJSONとEMPTYへ記録します。道路パノラマの2D確認とは別の処理です。清掃済みシーンのコピーを固定して再開用に保存し、材質やジオメトリを変更せず、照明配置や機械学習は行いません。

道路部分だけなら `python run.py roads "street_clean.blend" --output "新しい保存先"` です。HPCの既存SSH接続と、既存LLM API設定を使います。学習そのものは実行しません。Airは既定1.0、Aerosols・Ozoneは既存のCAMS換算です。

一括実行から呼ぶプログラムは次のとおりです。各工程の保存済み結果を次へ渡します。

| 工程 | 実行する既存プログラム・接続先 |
| --- | --- |
| 地名・季節・時間帯の抽出 | `conversation.extract` → HPC LLM API |
| 緯度・経度・標高・タイムゾーン | `conversation.resolve_location` → `geocoding` → Open-Meteo |
| 日付・時刻の選択 | `date_selection` / `time_selection` / `solar_math` |
| 気象・大気の取得 | `weather_reference` → Open-Meteo、`atmosphere_reference` → CAMS EAC4 |
| 世界・GLB | `world generate` → `world mesh` → World Labs API |
| 蓋除去・建物の保護・破片清掃・消灯 | `prepare_world` → `sky_cleanup` / `sky_cleanup_review` / `sky_cleanup_polish` / `finalize_world_blender` / `relight_world_blender` |
| 窓・街灯・ランタンの3D検出 | `scene_inventory` → HPC VLM → `scene_inventory_blender` → JSON・EMPTYマーカー |
| 道路と分岐の探索 | `roads/explore_world.py` → `render_prague_scan.py` → `Invoke-Mask2Former.ps1` / `detect_ground.py` → `project_road.py` |
| 道路上の撮影位置・360度画像 | `roads/walk_panorama.py` → `capture_plan.json` |
| 大気・太陽・検出マーカーを持つ初期状態 | `build_world_blender` → `sun_sky` / `atmosphere_mapping`、カメラ位置の土台を `camera.json` へ保存 |
| 本撮影（指定時のみ） | `capture_world_blender.install_cameras` → `render_views` |

Blenderの `scripts/prague_lighting.py` も、その世界に保存した大気データとカメラ計画を使います。

清掃の建物保護候補は `sky_semantics.py` で元画像のHPC道路認識モデルによる空分類と、HPCが指定した空の点からの色の連続性を照合します。真上の灰色の蓋を建物と見誤って保護し続けることを抑え、照合画像は清掃チェックポイントの `semantic_guard/` に保存します。仕上げの小片除去も、他の視点で確認した建物の保護範囲を尊重します。
保護範囲の端や角に触れる三角形も残します。範囲が面の半分未満だからと削除して、壁の境界に同じ穴を開け直すことを防ぎます。
HPCが指定した範囲は、前後画像で新しく空洞になった画素とも照合します。既に残っている壁や元から空だった部分だけの指定は、追加の修復として数えません。
建物の保護がある場合は、輪郭を滑らかにする頂点移動も省いて元の形を優先します。

## 既に接続されていた大気付きの一括撮影

`run.py capture` が、**元GLB → 蓋除去・消灯 → 保存済みCAMS大気を空へ反映 → 道路視点で撮影**をつなぐ既存の入口です。清掃済み `.blend` を渡した場合は清掃を省きます。

以前のプラハを再撮影する、ファイルが実在する実行例：

```powershell
cd C:\Users\Astra\Desktop\prague
.\.venv\Scripts\python.exe -X utf8 run.py capture `
  "outputs\sky_cleanup\20261104_175932_038028_polished\street_relight.blend" `
  --capture-plan "outputs\prague_daylight\capture_plan.json" `
  --atmosphere-reference "outputs\prague_daylight\eac4-prague-2025-07-15.json" `
  --settings "config\prague_capture.json" `
  --output "outputs\prague_daylight\renders"
```

`config/prague_capture.json` で標高202mを渡します。元の保存済みEAC4 JSONには標高がないため、これを省くと今回の202m指定にはなりません。この撮影計画は以前の街専用です。新しく生成した街には、その街の道路視点で作成した計画を使います。

呼び出しは `capture_world.main()` → GLBなら `prepare_world.main()` → `capture_world_blender.main()` → `render_views()` → `initial_atmosphere()` / `set_sun()` / `set_sky()` の順です。撮影用の一時シーンへ反映し、元の暗い `.blend` を上書きしません。

旧フォルダの実行記録 `C:/Users/Astra/Desktop/test1/scratch/combined-street-capture/renders/shot-yu1mqz9w/settings.json` でも、12視点、標高202m、Aerosols約28.6513、Ozone約0.941766の反映を確認しました。世界生成APIの呼び出し、地名APIの呼び出し、新規CAMS取得はこの `capture` コマンドの内部にはなく、保存済み入力を渡す仕組みです。

## プロンプトからGLB・清掃・消灯まで

1. `config\world_prompt.txt` を編集して保存します。
2. PowerShellで以下を実行します。

```powershell
cd C:\Users\Astra\Desktop\prague
.\.venv\Scripts\python.exe -X utf8 run.py world generate
```

ここでは世界の生成とワールド情報・閲覧URLの保存までです。GLBはまだ取得しません。完了済みの状態でこの生成コマンドをもう一度実行すると、新しい世界を生成します。既存の結果を確認するだけなら `world generate --resume` を使います。

3. テクスチャ付きGLBを作成・取得します。

```powershell
.\.venv\Scripts\python.exe -X utf8 run.py world mesh
```

最新の通常生成結果が対象です。別の結果と取り違えないようにする場合は、生成時に表示された保存先を明示します。

```powershell
.\.venv\Scripts\python.exe -X utf8 run.py world mesh --run-dir "outputs\worlds\生成時に表示されたフォルダ"
```

4. 上で保存された `street_textured.glb` を次へ渡します。以下のパスは実際の保存先に置き換えます。

```powershell
.\.venv\Scripts\python.exe -X utf8 run.py prepare "outputs\worlds\生成時に表示されたフォルダ\street_textured.glb"
```

または `scripts\windows\GLBを準備.cmd` に元のGLBをドラッグします。

`prepare` の内部は自動です。蓋の除去、残った破片の清掃、Principled BSDFへの変換、自発光・環境光ゼロ、カメラ・ライト除去まで進みます。画面に表示された `outputs\sky_cleanup\..._polished\street_relight.blend` が照明作業用、`street_relight.glb` が清掃・材質変換済みGLBです。元のテクスチャに描き込まれた明暗は残ります。

`world generate`、`world mesh`、`prepare` は現在それぞれ別の入口です。通常の `generate` 一回で後続全部を実行する機能にはなっていません。

モデルをPlusにする場合は、新規生成時だけ次のように指定します。

```powershell
.\.venv\Scripts\python.exe -X utf8 run.py world generate --model marble-1.1-plus
```

SPZが必要な場合だけ次を使います。これはGLB取得とは別です。

```powershell
.\.venv\Scripts\python.exe -X utf8 run.py world download --asset splats
```

## 地名、標高、太陽・空、大気データ

地名検索だけを行う場合：

```powershell
.\.venv\Scripts\python.exe -X utf8 run.py location Prague --country CZ
```

緯度・経度・標高・タイムゾーンの候補を返します。このコマンドだけではBlenderを変更しません。また、通常の `world generate` はプロンプトから地名を抽出しません。

会話JSON経由の `conversation process` には、地名抽出 → 地名検索 → 季節・時間帯からの日付・時刻選択 → `scene_conditions.json` / `render_settings.json` 保存がつながっています。Blenderで `scripts\prague_lighting.py` を実行し「会話の条件」を反映すると、地点・標高・日時に基づく太陽と空を反映できます。この経路もCAMSデータ取得は別です。

大気データを個別に取得する例です。以下の2026年7月15日12時は一括実行の初期設定と同じ日時です。プラハは設定済みの既定地点です。既定では同じ月日・現地時刻に対応する2025年のCAMS EAC4を参照します。

```powershell
.\.venv\Scripts\python.exe -X utf8 run.py atmosphere --date 2026-07-15 --time 12:00
```

会話処理で日時を選択済みなら、その保存結果を渡せます。

```powershell
.\.venv\Scripts\python.exe -X utf8 run.py atmosphere --from-time "outputs\conversations\対象のID\time_selection.json"
```

`atmosphere` はデータ取得・JSON保存までです。取得したEAC4を空へ反映して時間帯を選ぶ既存関数もあります。清掃済みのBlenderファイルを開き、BlenderのPythonコンソールで実行します。JSONパスは実際に `atmosphere` が保存したものへ置き換えます。

```python
import sys
sys.path.insert(0, r"C:\Users\Astra\Desktop\prague\src")
from streetlab.daylight import set_world_time
set_world_time("昼", r"C:\Users\Astra\Desktop\prague\outputs\atmosphere_reference\実際の保存ファイル.json", altitude=202.0)
```

この関数は参照地点・日付から昼の太陽位置を選び、標高とEAC4のAOD・オゾンから空の初期倍率を設定します。「昼」はその日の太陽南中付近です。「朝」「夕焼け」「夜」なども指定できます。関数は現在のシーンを変更するので、残す場合はBlenderで保存します。

撮影用の `capture` にも、保存済み大気JSONを `--atmosphere-reference` で渡して空へ反映する処理があります。こちらは対象の街で作成した道路撮影計画 `--capture-plan` も必要です。

操作パネルは、一括実行で保存したその街自身の大気JSON・カメラ計画を優先します。旧シーンでは `outputs/prague_daylight` を従来どおり使います。新しい街の計画作成は一括 `build` が担当し、パネル自体は道路探索を実行しません。

詳しくは [世界生成](WORLD_GENERATION.md)、[蓋除去と消灯](SKY_CLEANUP.md)、[会話入力](CONVERSATION_INPUT.md)、[大気データ](ATMOSPHERE_REFERENCE.md)、[太陽と空](SUN_SKY.md) を参照してください。

## 今回完成したプラハ

今回だけの実行記録は `outputs/world_batches/20261109_185145/batch.json`、世界の保存先はその下の `prague/` です。通常の最新結果選択の対象とは別なので、この結果を指定するときは `--run-dir outputs/world_batches/20261109_185145/prague` を付けます。

一括実行 `outputs/builds/20261109_194114_862991` は完了しています。地点はプロンプトから既存HPCで抽出し、プラハの代表地点（緯度50.08804、経度14.42076、標高202m、Europe/Prague）を取得しました。描画日は2026-07-15現地12時、大気の参照年は2025年です。

照明ゼロのBlender/GLBは `outputs/sky_cleanup/20261109_231930_812105_polished/street_relight.blend` / `.glb`、大気の初期状態は一括実行フォルダの `street_initial.blend` です。完成写真12枚と撮影設定は `renders/shot-ifc9ycur/settings.json`、道路地図・5経路・12地点の計画とHPC確認結果は `roads/9f94f4071affce2b/` にあります。別の試写フォルダは調整前の記録です。

検証は `verification.json` に保存しています。保存シーンはカメラ・ライト0、消灯とSunによる再照明を確認済みです。プラハのWorld Labs消費は5,080クレジット、そのキーで最後に照会した残高は1,920です。

## 次のキーで生成したウィーン

7,000クレジットの残高を確認して `outputs/builds/20261109_234836_223354` で実行しました。地点はVienna / AT、標高171m、Europe/Viennaです。世界生成とGLB取得は完了し、約131.5MiBの `world/street_textured.glb` を保存済みです。PCスリープ後に同じ保存先で再開しました。状態は同フォルダの `build.json`、現在のログは `build_connected.log` です。生成済み世界は [ビューアー](https://marble.worldlabs.ai/world/c1d2bf10-6f5b-45c0-b6e9-2a9b6efba147) にあります。

最初の `light` エラーは、条件抽出に生成文用の厳格な本文検査を流用したためで、修正済みです。元のウィーン用プロンプトを保持したまま再開しました。再開する場合は `python run.py build --resume --run-dir outputs/builds/20261109_234836_223354` です。

## ドイツ・ローテンブルク

新しいキーの7,000クレジットを確認し、`outputs/builds/20261110_083134_011934_rothenburg` で生成を開始しました。地点はRothenburg upon Tauber / DE、標高434m、Europe/Berlinです。World IDは `fe01bc11-72e5-4dea-970a-99fa00a4357c`。既存の生成・GLB処理記録を保存しているので、再開は `python run.py build --resume --run-dir outputs/builds/20261110_083134_011934_rothenburg` です。進行状況は `build.json` と `build_connected.log` を参照してください。ウィーンとドイツは本撮影を省く設定です。
