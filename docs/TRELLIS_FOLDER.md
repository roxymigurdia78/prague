# フォルダからTRELLIS.2用画像を作る

`scripts/windows/start_trellis.cmd` をダブルクリックして監視を起動します。開いた画面は動かしたままにしてください。
閉じる、または Ctrl+C で停止します。Windowsへの自動起動登録はしていません。

## 写真の入れ方

- `C:\Users\Astra\Desktop\prague\trellis_input` に同じ対象の写真を1〜16枚入れます。
- 例：木のドアのアップ、街並みの中の同じドア、斜めからの同じドア。
- 別の対象を処理するなら `trellis_input\door`、`trellis_input\lamp` のように対象ごとのサブフォルダを作ります。
- 直下の写真はまとめて1対象、各サブフォルダもそれぞれ1対象です。さらに深いフォルダは読みません。
- PNG/JPG/JPEG/WEBPに対応します。SPZは直接渡せません。
- 写真が15秒間追加・更新されなくなると、その組み合わせを処理します。
- 大量のコピーや写真の追加に時間がかかる場合は、入力フォルダの外で写真を揃えてからフォルダごと移すと確実です。

写真の追加で組み合わせが変わると、新しい処理になります。最初の処理が済んだ後で同じ対象の写真を追加すると、もう一度料金がかかります。1対象の写真を揃えてから投入してください。

## 自動で行うこと

1. EXIFによる向きを補正し、API用に長辺2048px以内のコピーを作ります。元写真は変更しません。
2. KambeHPCのVLMが全画像を分析し、対象・形・劣化・材質や照明の仮説・不確かな点を整理します。既存のHPC設定では `qwen3.5:35b` を使います。
3. 対象全体が見える最も適した画像を選び、それを第一参照、他を補助参照として画像編集API（初期値 `gpt-image-2`）に渡します。
4. 元の形や古さを保ち、スプラットのぼけや浮遊ノイズ、強い影・色かぶりを抑えた、白背景の単一対象画像を1枚作ります。
5. `C:\Users\Astra\Desktop\prague\outputs\trellis\画像セットの識別子\trellis_input.png` に保存します。

白背景はTRELLIS.2の必須条件ではありません。背景除去を使う一般的な入力として初期設定にしています。
透明PNGにしたい場合は `config/trellis.local.json` の `background` を `transparent` にしてください。
出力に透明部分がない場合は成功扱いにせず停止します。透過対応は指定モデル・アカウントに依存します。

同じフォルダの `manifest.json` に分析結果、指示、状態、API使用量を、`references` に送信時の参考画像を残します。
「対象が複数あって特定できない」場合は `needs_review` として画像編集を止めます。その場合もHPCの分析処理は実行済みです。
推測で細部を補う処理のため、元の物体の正確な復元や、照明の完全な分離は保証できません。出力を元画像と比較してください。
3D化・TRELLIS.2への自動送信・SPZ内の差し替えはこのツールの範囲外です。

## 設定

`C:\Users\Astra\Desktop\prague\config\trellis.local.json` を編集し、監視を再起動すると反映します。

- `target_hint`: 通常は空欄で自動判定。対象が曖昧なら `右側のアーチ型の木のドア` などを記入。
- `include_frame`: `true` はドア・窓のすぐ周囲の枠も含めます。扉本体だけなら `false`。
- `quality`: 初期値 `high`。試作時は `low` または `medium`。
- `settle_seconds`: 写真の追加・更新を待つ秒数。
- `vision_model`: 空欄ならHPC設定の `LLM_MODEL` を使います。ここに明示すると優先されます。
- `hpc_base_url`: 空欄ならHPC設定の `LLM_BASE_URL` を使います。
- `hpc_env_file`: HPCのキー・接続先・モデルを読むファイル。現在は既存の `C:\Users\Astra\Desktop\sao\mvl-skeleton\python\.env` を指定しています。キーのコピー・元ファイルの変更は行いません。
- `image_model`: OpenAIの画像編集モデル。初期値は `gpt-image-2`。

## キー・費用・再実行

画像編集は既存の `C:\Users\Astra\Desktop\prague\.env` にある `OPENAI_API_KEY` を使います。
画像分析はHPC用の `LLM_KEY`（未設定時は `LITELLM_API_KEY`）を使います。OpenAIキーをHPCへ送信する代替処理はありません。
HPCの各設定は環境変数（pragueの.envも含む）が指定されたHPCファイルの値より優先されます。
HPCファイルから読むのは `LLM_KEY` / `LITELLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` だけです。
キーはログや結果に保存しません。画像編集用の接続先とキーはHPCとは独立しています。
1画像セットにつきHPCでのVLM分析とOpenAIでの画像編集を各1回実行します。画像編集は有料です。HPC利用条件は既存の契約・割当に従います。
HPCの応答はChat Completionsのストリーミングで受け取り、JSONを検証します。分析が失敗した場合はOpenAIのVLMへ切り替えず停止します。
自動の品質評価や再生成は行いません。

画像を送らずHPCの接続先・モデル一覧を確認する場合：

```powershell
python run.py trellis --check-hpc
```

同じ画像セットは、ファイル名や順序を変えても、監視を再起動しても再送しません。
設定だけ変更した場合も再送しません。入力の内容が変わると別の画像セットとして扱います。
通信失敗・中断・対象不明の結果も記録し、自動再送しません。
失敗したものを再処理するときは、既存の結果を確認したうえで、別の出力先を明示してください（新しい有料処理になります）。

```powershell
python run.py trellis --once --output-dir outputs/trellis_retry
```

API接続エラーでは監視全体を停止し、ほかの画像セットへの送信も止めます。
複数の監視を同じ出力先で動かした場合も、同一画像セットは1つだけが予約して処理します。

APIを呼ばずに入力のまとまりを確認する場合：

```powershell
python run.py trellis --dry-run
```

現在の写真だけを一度処理して終了する場合（コピー完了を確認するため待機します）：

```powershell
python run.py trellis --once
```

Python環境を変えた場合の依存ライブラリ：`python -m pip install openai Pillow`

## 参照した仕様

- https://developers.openai.com/api/reference/python/resources/images/methods/edit
- https://developers.openai.com/api/docs/guides/images-vision
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://github.com/microsoft/TRELLIS.2/blob/main/trellis2/pipelines/trellis2_image_to_3d.py
