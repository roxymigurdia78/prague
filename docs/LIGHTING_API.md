# 学習済みの照明モデルを公開してループから使う

Windowsがつまみを変えて道路から撮影し、HPCの比較サービスが2枚のどちらを選ぶか返します。HPCは最初の比較でQwenとLoRAを読み込み、次の比較にも使います。モデル一覧だけの確認ではGPUを使いません。

## HPCの起動

配置先は `~/light_eval/lighting_api/` です。ターミナルで次を実行します。GPUが割り当てられている環境を使ってください。

今回のセットアップでポート23002にサービスを起動済みです。以下はHPC再起動後などにサービスを起動するコマンドです。現在のポートとPIDは `service.json`、起動・推論の記録は `server.log` にあります。

```bash
cd ~/light_eval/lighting_api
../.venv312/bin/python lighting_api.py --port 23002
```

ポータルの「自作API公開」で、このプログラムのポート `23002` を登録します。公開URLをWindows側の `LIGHTING_API_URL` に入れます。ポートが使用済みなら空きポートを `--port` に指定し、その番号を公開します。

### ノートから停止・再起動する

HPCの `~/light_eval/lighting_api_stop.ipynb` は停止用、`~/light_eval/lighting_api_restart.ipynb` は起動・再起動用です。それぞれ説明の下のコードセルを1回実行します。ノートはHPCにだけ保存しています。学習前は停止用ノートでAPIを止めてGPUメモリを空けます。学習後は学習ノートのカーネルを終了してから再起動用ノートを実行します。他のカーネル、保存済みモデル、モデル登録、トークンは変更しません。

停止用は自分の照明APIのプロセスだけを確認して停止し、停止済みならそのまま終了します。再起動用はAPIを停止してから同じポートで起動し、認証付きヘルスチェックで正常起動を確認します。ポートは `service.json` の値（未登録なら23002）を使い、起動したPIDも同ファイルに記録します。APIはノートのカーネルと別のプロセスで動き、モデルは次の画像比較で読み込みます。ポータル側の公開を停止していた場合は、公開も別途再開してください。

`api.token` にサービス専用の認証値を保存します。初期作成は `../.venv312/bin/python lighting_api.py --init-token` です。Windowsの `LIGHTING_API_TOKEN` に同じ値を設定します。CloudflareのService TokenのClient ID・Client Secretはそれぞれ `LIGHTING_CF_CLIENT_ID` と `LIGHTING_CF_CLIENT_SECRET` に入れます。Cloudflareの認証ヘッダーは公式のService Token方式に合わせています。[Cloudflareの説明](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)

公開URLに `/hub/user-api/` が入るJupyterHub経由の形式では、ポータルのJupyterHub API Tokenを `LIGHTING_JUPYTER_TOKEN` に追加します。クライアントは `Authorization` にJupyterHubトークン、`X-Lighting-API-Token` に比較サービス用トークンを送ります。両方の認証を通してモデル一覧や比較を呼び出します。403が出る場合は、このトークン、公開URLのユーザー・API名、Cloudflareの認証設定を確認します。[JupyterHubのトークン認証](https://jupyterhub.readthedocs.io/en/stable/howto/rest.html)

接続にはアプリを識別する `User-Agent: Streetlab-Lighting/1.0` を付けます。この公開経路ではPython標準のUser-AgentがCloudflareの1010で拒否されることを確認しています。トークンを替えても403が続く場合は、1010かどうかも確認します。[Cloudflareの1010の説明](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/)

## モデルの追加と切り替え

HPCの `models.json` がモデル名とLoRAの保存先を対応づけます。`a_all` と `a_match` を登録します。

```json
{
  "default_model": "a_all",
  "models": {
    "a_all": {
      "base_model": "~/light_eval/models/qwen3.8-27b",
      "adapter": "~/light_eval/outputs/aachen_test2_pair_v1/A_all/adapter",
      "long_edge": 768
    },
    "next-model": {
      "base_model": "~/light_eval/models/qwen3.8-27b",
      "adapter": "~/light_eval/outputs/次の実験/adapter",
      "long_edge": 768
    }
  }
}
```

新しい学習が終わったら、新しい名前と保存先をHPCの `models` に追加して保存し、Windowsの `.env` の `LIGHTING_MODEL` をその名前に変更します。次のループ起動からそのモデルを使います。APIサーバーは要求ごとに設定ファイルを読み直すので、設定の変更後にサーバーを再起動する操作は不要です。同じ名前の保存先を変更した場合も、次の比較でLoRAを切り替えます。切り替えでは元モデルへLoRAを統合せず、現在のLoRAを外して次を読み込みます。[PEFTの切り替え機能](https://huggingface.co/docs/peft/main/package_reference/lora)

Windowsのモデル名は必須です。`LIGHTING_MODEL=a_all` またはコマンドの `--model a_all` で指定します。空欄の場合は設定を求めて停止します。`default_model` はAPIを直接使う場合の既定値です。

実行中の探索はモデル名とLoRAの版を記録します。途中でモデルが更新された場合は判定の混在を防ぐため、その探索を停止します。新しいモデルで試す場合は再開元を指定せず開始します。モデル名と日時のフォルダを自動で作るので、過去の結果も残ります。過去の探索は、そのモデル・版のまま `--resume "その実行フォルダ"` で新しい日時の保存先へ引き継げます。

## Windowsの設定と実行

`config/lighting_api.env.example` の項目を `prague/.env` に追加します。

```dotenv
LIGHTING_API_URL=https://ポータルで作成した公開URL
LIGHTING_API_TOKEN=HPCのapi.tokenの内容
LIGHTING_MODEL=a_all
LIGHTING_CF_CLIENT_ID=CloudflareのClient ID
LIGHTING_CF_CLIENT_SECRET=CloudflareのClient Secret
LIGHTING_JUPYTER_TOKEN=ポータルのJupyterHub API Token
```

公開URLが設定されると照明探索が自作APIを使います。道路認識などの既存のHPC設定はそれぞれの処理で使います。

```powershell
.\.venv\Scripts\python.exe -X utf8 run.py lighting-api-check
.\.venv\Scripts\python.exe -X utf8 run.py lighting-search "outputs/builds/対象の保存先" --rounds 1 --output "outputs/lighting_trials"
```

`--output` は親フォルダです。その下に `a_all_YYYYMMDD_HHMMSS` の実行フォルダを自動で作り、各回の結果はその中の `index.html`、過去の一覧は親フォルダの `lighting_history.html` に保存します。日時は日本時間です。

通常はそのワールドの初期設定から新規探索し、時間指定なしなら30分、`--minutes 120` なら120分探索します。一括の `conversation-build --with-lighting-search` / `build --with-lighting-search` では `--lighting-minutes 120` で時間を変えられます。同じモデル・同じワールドの中断再開だけ、明示的な `--resume` を使います。モデルを替えた実行は、新しいモデルで新規探索します。

前方と後方の画像は方向ごとに2枚比較し、両方向の判定が一致した場合にその勝者を採用します。画像の左右順を入れ替えても確認します。確認が引き分けや不安定な場合は、同じパノラマの90°・270°の方向も追加します。追加方向は別のキャッシュと `directions` に記録し、元の視点で明確に反対の選択があれば一方へ強制しません。APIへ渡す形式は同じ2画像比較です。判定の記録にはモデル名と版、各方向の結果が入ります。モデルから出力するのは1・2・3の選択で、理由欄には方向ごとの判定を記録します。学習時と同じ質問・思考なし・回答トークンの比較を使います。街の条件と道路からの切り出しは実行する街の設定です。

## 接続の確認

認証付きの `GET /health` でサービスの起動を確認し、`GET /v1/models` で名前と版を確認できます。`POST /v1/compare` の入力はモデル名・版・context・base64画像2枚です。版は `/v1/models` の返却値を使います。最初の比較では元モデルの読み込み時間がかかります。

Qwen本体は前回の計測で約51 GiBを使います。GPUの空きが少ない場合は、使い終わった学習・評価用Jupyterカーネルを終了してから比較を試します。今回のセットアップ時はGPUの空きが約1.6 GiBで、推論を始める前まで確認しました。

同じ元モデルを使う新しいLoRAは随時追加できます。異なるモデル構成や回答形式を使う実験では、そのモデルに合う入力処理と評価も確認してから登録します。
