# 小物画像の生成・編集

フォルダへの投入からVLM分析・画像編集まで自動で行う場合は、新しい `scripts/windows/start_trellis.cmd` を使ってください。
写真1〜16枚を同じ対象の参考画像としてまとめて処理します。使い方は [TRELLIS_FOLDER.md](TRELLIS_FOLDER.md) を参照。
以下は従来の1枚入力・手動実行スクリプトの説明です。

`src/streetlab/generate_material_variants.py` は、元の `sao/mvl-skeleton/python` のスクリプトをもとにした画像生成用ファイルです。
TRELLIS.2へ渡すPNGを作ります。SPZ内の物体選択・削除、GLB生成、HPCへの送信はまだ行いません。

## 準備

```powershell
cd C:\Users\Astra\Desktop\prague
```

このフォルダの既存の `.env` に、次の行を**追加**してください。既存のWorld Labsキーはそのまま残します。

```dotenv
OPENAI_API_KEY=ここにOpenAIのAPIキー
```

World Labsのキーとは別のキーです。すでに環境変数にある場合はそちらを優先します。
既存のキー入りファイルを明示して使う場合は `--env-file "C:\path\to\.env"` を付けられます。
他のフォルダのキーを自動で読み込んだりコピーしたりはしません。

画像用ファイルではOpenAIのPythonライブラリを使います。現在の環境にはインストール済みです。
別の環境で必要な場合だけ、`python -m pip install openai` を実行してください。

## 最初は花瓶を1枚、新規生成

```powershell
python run.py images --classes vase --variants 1
```

引数なしでも花瓶を1枚生成します。初期設定は元のスクリプトと同じ `gpt-image-2`、低品質設定、1024×1024、白背景です。
品質優先なら `--quality high` を追加します。モデルは `--model` で変更できます。

元のメモ帳と同じ使い方もできます。これは合計6枚を生成します。

```powershell
python run.py images --classes window ceiling_light --start-variant 1 --variants 3
```

## SPZの小物を、指示なしで同じ物として鮮明化する

対象の小物が全体まで見えるように撮影・切り出した画像を、このフォルダに `reference.png` として保存します。
大きな街並み全体ではなく、まずは小物1つがはっきり写った画像を使ってください。

```powershell
python run.py images --input reference.png --quality high
```

`--prompt` は不要です。画像を見て同じ小物の形・色・素材・元の世界の画風を保ち、ぼけやスプラット由来のノイズを減らして白背景の参照画像を作る、という共通指示を自動で付けます。
共通指示はスクリプト内の `ENHANCE_PROMPT`、APIへ送った全文は `manifest.json` の `assets[].prompt` に残ります。
単なる画素数の拡大ではなく生成による補完なので、元画像にない正解の細部や裏側を復元できる保証はありません。
実際に同じ物に見えるか、高精細になったかは結果を比較して判断してください。

このモードではモデルが入力画像を直接解釈します。別のVLMを呼んで説明文に置き換える工程は入れていません。
SPZからの自動撮影・物体選択・切り出しは未実装です。これらができた後は、切り出した画像をこのコマンドへ自動で渡す構成にできます。

## 明示的にデザインを変更する場合だけ --prompt を付ける

```powershell
python run.py images --input reference.png --prompt "この花瓶を青い陶器に変更。形と向きを維持する" --name blue_vase --variants 1 --quality high
```

`--input` を付けた場合は画像編集API、付けない場合は新規生成APIを使います。
画像入力だけなら同じ物の鮮明化（`enhance`）、変更指示も指定するとデザイン編集（`edit`）としてmanifestに記録します。
入力はPNG/JPG/WEBPです。SPZやGLBを直接読み込む機能ではありません。
編集では参考画像の視点を保ち、対象物だけを白背景に取り出す指示を追加します。形や向きの完全な一致を保証するものではありません。
複数バリエーションは同じ元画像から独立して作ります。

透明背景を試す場合は、上記コマンドに `--background transparent` を追加してください。
透明背景の対応は指定したモデル・アカウントで変わるため、最初は白背景を推奨します。

## 保存先と確認

`outputs\images\日時_material_variants\` に、PNGと `manifest.json` を保存します。日時は日本時間です。
同じ秒に実行しても別フォルダになります。元画像は変更しません。
manifestには入力画像のパス、モデル、各画像への指示、生成状態、返却された使用量などを記録します。APIキーは保存しません。

出力PNGをTRELLIS.2の入力に使い、GLBへの変換は次の工程で行います。

APIを呼ばずに指示と生成枚数だけを確認する場合：

```powershell
python run.py images --classes window ceiling_light --start-variant 1 --variants 3 --dry-run
```

`--dry-run` はAPIキー不要で、ファイルも作りません。
通常実行は有料APIを呼びます。失敗時は残りの生成を止め、自動再送しません。
通信失敗や中断でも、サーバー側で受け付け済みの場合は課金される可能性があります。
同じコマンドの再実行は新しい生成になります。先にmanifestと保存済みPNGを確認してください。

## 確認した資料

- https://developers.openai.com/api/reference/python/resources/images/methods/generate
- https://developers.openai.com/api/reference/python/resources/images/methods/edit

APIを呼ばない動作確認：

```powershell
python run.py test
```
