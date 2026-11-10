# 窓・街灯・ランタンの配置情報

目的は「どこに何があるか」を記録し、後工程から同じ位置・範囲を使えるようにすることです。
ライト・発光面・反射材質は追加しません。窓は暗い窓、反射している窓、室内光のある窓をすべて対象にします。

現在は `python run.py build` からも既定でこの検出を呼びます。一括実行では `outputs/builds/日時/inventory/` に結果を保存し、`street_initial.blend` にEMPTYマーカーを読み込みます。太陽・空と検出結果を持つ初期状態まで自動実行し、本撮影は `--with-capture` を指定した場合だけ追加します。普段の手順は [実行メモ](../実行メモ.txt) を参照してください。

```powershell
python -X utf8 run.py map
python -X utf8 run.py map "準備済みのstreet_relight.blend"
python -X utf8 run.py map --views-per-position 8 --rounds 2
python -X utf8 run.py map --continue-dir "outputs/scene_inventory/実行日時"
python -X utf8 run.py map --continue-dir "outputs/scene_inventory/実行日時" --rebuild
```

`scripts/windows/配置を記録.cmd` でも実行できます。旧入口の `src/streetlab/light_placement.py` と `scripts/windows/照明を配置.cmd` も現在はこの配置情報の処理を呼びます。
入力は準備済みBLENDまたはそのフォルダー。指定しなければ最新の `street_relight.blend` です。
元GLBの場合は先に `src/streetlab/prepare_world.py` を実行してください。

## 処理と成果物

複数位置・複数方向から検出用画像を作り、HPCのVLMで窓の開口と街灯・ランタンの器具を認識します。
撮影時だけテクスチャを見えるようにし、画像内の中心と枠をメッシュ表面へ投影します。
2周目は角度をずらして見落としを追加し、同じ場所の観測を統合します。

- `street_mapped.blend`: 元のメッシュ・材質と、`SEMANTIC_PLACEMENT` コレクション内のEmpty目印。
- `inventory.json`: 種類、3D座標、表面法線、領域の4隅、幅・高さ、元メッシュ、信頼度、観測視点。
- `WINDOW_001` など: 後から反射材質を割り当てるための窓領域候補。材質は未変更。
- `FIXTURE_001` など: 後から照明を置く器具の位置。ライトは未作成。
- `round_XX/*_mapped.png`: 窓を水色、器具を橙色で示す確認画像。
- `round_XX/detections.json`, `projected.json`: 検出・投影の根拠と棄却理由。

高信頼または異なる撮影位置から支持された対象は `status=supported`、それ以外は `review` と記録します。
座標・幅・高さはBlenderのワールド単位です。信頼度をモデルが省略した場合は保守的な内部値0.7と `confidence_source` を記録します。中心座標が検出枠の外にある応答は枠の中点へ補正し、元の中心と補正理由も残します。
後工程はID・種類・statusを参照できます。姿勢は表面の外向き法線に合わせます。
太陽が画像に実際に見えた場合は方向情報だけを保存し、SUNライトや架空の3D位置を作りません。

保存シーンは暗いままです。配置をBlenderで見るため、ビューポートの表示モードだけMaterial Previewに設定します。
検出用の自発光設定は撮影用プロセスだけのもので、最終BLENDへ保存しません。

## 精度と再開

窓の範囲は検出枠を表面へ投影した近似矩形です。ガラスをピクセル単位で切り出したマスクではありません。
`face_ids` は枠内のレイが当たった面の参考情報で、枠や壁を含む可能性があります。面全体をガラスに変えてよいという指定ではありません。
窓が壁と一体メッシュでも位置・範囲を記録できます。厳密な反射材質の領域分けはこの情報を使って別工程で行います。
VLMの見落としや誤検出、復元メッシュの凹凸による位置・向きの誤差は残ります。

標準は8方向×複数撮影位置×2周、`--rounds` は1〜4、`--views-per-position` は4〜16。
2画像ずつ分析し、ID不足などの場合は1枚ずつ確認します。生応答を保存し、再開時に有効な応答を再利用します。
`--prepare-only` は撮影だけ。`--reuse-scans` は同じ入力ハッシュ・周回数・方向数の過去の検出用画像だけを再利用します。
`--rebuild` は保存済み検出から統合と3D配置を再計算し、HPCを呼びません。
古い「発光している物体だけ」の検出結果は再利用せず、全窓を改めて分析します。

HPC接続と標準モデル `qwen3.8:27b` は既存の空除去処理と共通です。APIキーは成果物へ保存しません。
以前の `outputs/light_placement/` は過去の照明試作で、今回の成果物は `outputs/scene_inventory/` に保存します。

## 実行確認（2026-11-04）

`outputs/scene_inventory/20261104_201512_364149/street_mapped.blend` が配置情報だけの結果です。
24視点・2周のHPC検出を統合し、窓103候補、器具5候補を記録しました。全108候補のうち56はsupported、52はreviewです。全窓の正確な個数を保証する数字ではありません。
単体テスト5件を通過。実際のBLENDを読み直し、ライト0、追加発光面0、カメラ0、環境光0、元の頂点座標・変換・材質ノード不変を確認しました。
検証記録は同じ実行フォルダーの `verification.json` です。
