# 街並みの差し替え実験

目的はプラハ風の街並みを生成し、建物・街灯・ベンチなどを選択、削除、別モデルへ置換できるかを確認すること。
生成文中の「個別に分かれた」という指定は構造を保証しない。実際のデータで検証する。

コマンドはユーザーがpragueのターミナルで実行する。World API用のファイルはPythonの標準機能だけを使う。

小物画像の生成・編集は `src/streetlab/generate_material_variants.py` を使う。こちらはOpenAIのPythonライブラリが必要。
使い方は [IMAGE_GENERATION.md](IMAGE_GENERATION.md) を参照。

GLBの上側の空をHPCのVLMで推定し、別の物体に分ける試作は [SKY_CLEANUP.md](SKY_CLEANUP.md) を参照。

Multiple Scatteringの9項目を関数から設定・部分更新・保存・復元する部品は [SUN_SKY.md](SUN_SKY.md) を参照。`sun_sky.set_sky()` を明示的に呼んだときだけWorldを更新します。

HPCのVLMで街灯・ランタン・すべての窓を検出し、位置・向き・範囲を3Dの目印とJSONへ記録するループは [LIGHT_PLACEMENT.md](LIGHT_PLACEMENT.md) を参照。`python -X utf8 run.py map` または `scripts/windows/配置を記録.cmd` で実行します。照明・発光面・反射材質は追加しません。旧名の `src/streetlab/light_placement.py` と `scripts/windows/照明を配置.cmd` も配置情報だけを記録する処理へ接続しています。

GLBを読み込んで「蓋の除去 → 残った破片の清掃 → 自発光をなくして照明待ち」にする一括処理は、`scripts/windows/GLBを準備.cmd`に元のGLBをドラッグするか、`python -X utf8 run.py prepare "対象.glb"`で実行します。最終ファイルは日時フォルダーの`street_relight.blend`です。撮影用カメラ・ライトを残さず、環境光0で保存します。手順と限界は [SKY_CLEANUP.md](SKY_CLEANUP.md) を参照してください。

1. `.env`にWorld Labs Platformのキーを設定する。
2. `python run.py credits`でAPI残高を確認する。
3. `config/world_prompt.txt`に作りたい世界の説明文だけを書いて保存し、`python run.py world generate`を実行する。
   名前は日時から自動設定する。モデルは標準で`marble-1.1`、非公開、seedは42で固定。名前やJSONを毎回編集する必要はない。旧`config/world_prompt.legacy.json`は以前の設定として残してあるが、今後の生成には読み込まない。
   テキストからMarble 1.1で1つ生成する。掲載料金は1,580クレジット。
   日本時間の開始日時で`outputs/worlds/20261104_153000/`のようなフォルダーを自動作成し、ワールド情報と閲覧URLを保存する。同じ秒に開始した場合は連番を付ける。画像や3Dデータは自動取得しない。
   取得するデータを選んだ後、次のいずれかを実行する。
   - `python run.py world download --asset splats`：SPZ
   - `python run.py world download --asset collider`：衝突判定用GLB
   - `python run.py world download --asset panorama`：パノラマ画像
   - `python run.py world download --asset thumbnail`：サムネイル画像
   取得先は自動で最新の生成フォルダーになる。SPZなども同じフォルダーにまとまる。
4. 見た目を持つGLBを調べるには`python run.py world mesh`を実行する。
   HQメッシュ書き出しの掲載料金は追加3,500クレジット。合計は5,080クレジット。
   返却されたファイルを`street_textured.glb`として保存する（実際のURLの拡張子を優先）。

以前に生成したプラハの結果は`outputs/prague`に保存済み。日時フォルダーがまだなければ、取得コマンドはこの結果を使う。
掲載料金は2026-10-28に公式資料で確認した値。実行時にAPI残高を取得し、確定コストも保存する。
https://docs.worldlabs.ai/api/pricing
https://docs.worldlabs.ai/api/reference/worlds/export

## プロンプトとモデル

普段は`config/world_prompt.txt`だけを書き換える。改行や引用符はそのまま使える。
`python run.py world generate --dry-run`でAPIを呼ばずに送信内容を確認できる。
実際に送った文章・名前・モデルなどは、日時フォルダーの`generate_request.json`に保存する。再開時はその保存済み内容を使い、後から書き換えたプロンプトで別の生成を始めない。

標準のMarble 1.1は固定料金で、テキストからの生成は1,580クレジット。
Plusは可能な場合に生成空間を自動拡張するモデルで、テキストからの生成は1,580〜3,080クレジット。
使う場合だけ`python run.py world generate --model marble-1.1-plus`を指定する。残高は料金の上限で確認する。
モデルと料金は2026-11-04に公式資料で確認した。
https://docs.worldlabs.ai/marble/models
https://docs.worldlabs.ai/api/models
https://docs.worldlabs.ai/api/pricing

## Blenderでの確認

- GLBをインポートし、Outlinerと選択操作で建物や街灯が個別のオブジェクトか確認する。
- 一体のメッシュなら、編集モードの連結選択で対象物だけを選択できるか確認する。
- 対象を削除し、背後の壁・路面の欠損や対象の影が残るか確認する。
- 別モデルを置き、位置・大きさ・材質・照明が合うか確認する。
- 「最初から分離」「手動で分離可能」「背景の補修が必要」「分離困難」を記録する。

GLB形式であること自体は、意味のある単位に分かれていることを保証しない。
衝突判定用のcollider meshは見た目を持つHQメッシュと区別する。
SPZは外観の確認に利用する。元の応答とスケール情報はworld.jsonに残す。

## 中断・失敗時

最新の生成が中断中なら、`python run.py world generate`は保存済みの処理を再開する。送信結果不明の記録があれば停止する。
最新の生成が完了済みなら、`python run.py world generate`は新しい日時フォルダーで新規生成する。
完了済みの結果を確認するだけなら`python run.py world generate --resume`を使う。
ダウンロードだけやり直す場合も`python run.py world download --asset splats`などで対象を指定する。
送信結果が不明な場合は再送を止める。Platformで既存の処理を確認する。
過去の結果を指定する場合は`python run.py world download --asset splats --run-dir outputs/worlds/20261104_153000`のように指定する。以前のプラハは`--run-dir outputs/prague`で指定できる。`mesh`や`generate`にも`--run-dir`を指定でき、指定したフォルダーの処理記録を再利用する。
