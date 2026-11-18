# CAMS EAC4：エアロゾルとオゾン全量

地点・描画日時から、同じ月日・現地時刻に対応するEAC4の大気成分を取得する部品です。既定の参照年は**2025年**。描画する年は変更しません。

| 取得する変数 | 保存する値 | Blenderでの用途 |
| --- | --- | --- |
| `total_aerosol_optical_depth_550nm` | `aod550`、無次元 | Aerosols倍率を校正する根拠 |
| `total_column_ozone` | `total_column_ozone_kg_m2`と`total_column_ozone_du` | Ozone倍率を校正する根拠 |

元データは観測を数値モデルに取り込んだ**再解析**です。街角の実測値とは区別します。AODとDUはBlenderの密度倍率と異なる量なので、この機能では倍率への換算・Blenderへの適用はしません。会話からの条件抽出や雲の描画も別の機能です。

## 最初の準備

1. [ADS](https://ads.atmosphere.copernicus.eu/how-to-api)に登録・ログインし、Personal Access Tokenを取得します。
2. [EAC4のDownloadページ](https://ads.atmosphere.copernicus.eu/datasets/cams-global-reanalysis-eac4?tab=download)で利用条件を確認し、**Web上で手動同意**してください。プログラムから同意はしません。
3. プロジェクトの既存`.env`に次の2行を追加します。ほかのサービスのキーは残してください。トークンをチャットへ貼る必要はありません。

```dotenv
CDSAPI_KEY=ADSのPersonal_Access_Token
CAMS_EAC4_LICENSE_ACCEPTED=true
```

`CAMS_EAC4_LICENSE_ACCEPTED`は手動同意済みというローカル確認です。これをtrueにするだけではサーバー上の同意は完了しません。実際のアクセス権はADS側でも確認されます。

認証は環境変数、プロジェクト`.env`の順で読みます。キーが見つからない場合は標準の`~/.cdsapirc`も利用できますが、`url`がADSの公式URLである場合に限ります。`CDSAPI_RC`によるファイル指定にも対応します。EAC4の同意確認フラグは引き続き必要です。公開URLと既定年は`config/atmosphere_reference.json`に置き、秘密情報は置きません。

依存関係はPython 3.10以上のプロジェクト環境に追加します。

```powershell
python -m pip install -e ".[atmosphere]"
python -X utf8 run.py atmosphere --check-setup
```

`--check-setup`はキー・確認設定・ライブラリの**有無のみ**を表示し、通信やデータ取得はしません。APIクライアントとNetCDFライブラリは取得するときだけ読み込みます。

## 取得前に計画を確認する

```powershell
# 既定地点はconfig/time_selection.jsonのプラハ。描画日時に対し2025年を参照。
python -X utf8 run.py atmosphere --date 2026-07-15 --time 18:30 --dry-run

# 2006〜2025年の同じ月日・時刻を参照する計画
python -X utf8 run.py atmosphere --date 2026-07-15 --time 18:30 --past-years 20 --dry-run
```

`--dry-run`は認証情報や取得用ライブラリがなくても動き、ファイル作成・API呼び出しは行いません。要求する変数・範囲・UTC日時・最も近い格子点・サンプル時刻差を確認できます。

## データを取得する

```powershell
python -X utf8 run.py atmosphere --date 2026-07-15 --time 18:30
python -X utf8 run.py atmosphere --from-time "outputs/time_choices/保存したファイル.json"

# 気温・気圧・湿度と参照年を確実にそろえる場合はこちら
python -X utf8 run.py atmosphere --from-weather "outputs/weather_reference/保存したファイル.json"

# 必要な場合だけ、各年の同じ日を取得。採用年は引き続き2025年。
python -X utf8 run.py atmosphere --date 2026-07-15 --time 18:30 --past-years 20

# 取得した年から1年を抽選。AODとオゾンは同じ年・同じUTC解析から選ぶ。
python -X utf8 run.py atmosphere --date 2026-07-15 --time 18:30 --past-years 20 --sample-year --seed 42
```

`--from-weather`は気象結果の採用年・地点・描画日時を引き継ぎます。気象側でランダムに2014年を選んだ場合、大気成分も2014年になります。気象は最寄り1時間値、大気成分は最寄り3時間値なので、実サンプル時刻まで同一になるとは限りません。各結果の実際の時刻を記録します。

`--reference-year`で参照年を直接指定できます。`--from-weather`と年の上書き・再抽選は同時に指定できません。別地点は`--latitude`・`--longitude`・`--timezone`を一緒に指定します。保存済みの入力を使う場合は地点や描画日時を上書きできません。

Pythonからも呼べます。

```python
from streetlab.atmosphere_reference import plan_atmosphere, fetch_atmosphere

location = {"latitude": 50.08804, "longitude": 14.42076, "timezone": "Europe/Prague"}
plan = plan_atmosphere(location, "2026-07-15T18:30:00+02:00")  # 通信なし
result = fetch_atmosphere(location, "2026-07-15T18:30:00+02:00")
```

## 日時・座標・単位の扱い

- 同じ月日・現地時計時刻を参照年に写し、IANAタイムゾーンでUTCに変換します。最寄りの3時間解析を採用し、ちょうど中間なら早い方を選びます。差は最大90分、補間はしません。
- 2月29日やDST切り替えで存在しない参照日時は、単年なら停止、複数年なら理由を記録して除外します。既定年が除外された場合、勝手に別年へ切り替えず採用値をnullにします。描画時刻が重複する場合は`--fold 0`または`--fold 1`が必要です。
- 2026-07-15 18:30のプラハを2025年で参照すると、現地18:30＝UTC16:30です。最寄りの解析はUTC15:00（現地17:00、90分前）になります。
- 0.75度格子の最寄り地点を選び、その地点を囲む小さな範囲だけ要求します。APIが返した座標も照合・保存します。地名検索の座標を、気象モデルの格子座標として偽って記録しません。
- オゾンは元のkg/m²も保存し、`DU = kg/m² / 2.1415e-5`で換算します。地表付近のオゾン濃度μg/m³は受け付けません。
- 座標・UTC日時・変数・単位・欠測を検証します。ZIP内で変数が別のNetCDFに分かれていても読み取れます。ZIPの中のパスはファイルシステムに展開しません。

公式資料を確認した2026-11-05時点の収録範囲は**2003-01-01〜2025-12-31**です。取得対象のUTC日がこの範囲を外れる場合は停止・除外します。今後の延長は公式の収録範囲を確認したうえで設定ファイルを更新してください。

## 保存・再開・通信量

取得結果JSONは`outputs/atmosphere_reference/`、元のZIP/NetCDF・出典・取得日時・SHA-256・リクエストIDはその中の`cache/`へ保存します。結果JSONは上書きしません。キャッシュは地点の格子・解析日時・要求内容で共有し、読めることとハッシュを確認して再利用します。キャッシュの読み取りでは認証情報を要求しませんが、NetCDF読み取りライブラリは必要です。

1要求は**2変数・1解析時刻・小範囲**です。20年モードでも20日分の選択サンプルであり、20年全体や地球全域を取得しません。複数要求は並列実行せず順番に処理します。

公式クライアントを使い、HTTPエラーの自動再試行を無効化しています。新規データ要求はこのアプリ独自の控えめな上限として**直近24時間に20件**まで。これはADS全体の公式上限ではなく、SDKの状態確認・カタログ参照・ダウンロード通信の回数とも異なります。他アプリや同じアカウントの要求はローカルでは数えられません。

送信前にローカル台帳へ予約し、応答のリクエストIDを待機前に保存します。ダウンロード中断後は同じコマンドで既存のリクエストIDから再開します。送信時に接続が切れ、IDを受け取れなかった場合は結果が不確実なので自動再送しません。その場合はADSのYour requestsと`cache/requests.sqlite3`の該当要求を確認してから復旧します。回数制限の回避目的で台帳を削除しないでください。

NetCDFは単体またはZIPで読み、32 MiBを超える取得ファイル・展開後の総サイズを拒否します。ダウンロード自体の途中サイズ制限ではありません。今回の要求は非常に小さいため、大きな応答は想定外として扱います。

データにはCAMS/ECMWFの出典・DOI・CC BY 4.0・加工内容を記録します。公開・配布時も出典と加工の表示を引き継いでください。キーや一時的な署名付きダウンロードURLは保存しません。

## 検証

`python run.py test`の新しいテストはネットワークを差し替えて動作します。実際のNetCDF4バイナリの読み取り、ZIP分割、単位換算、欠測・誤座標・誤時刻の拒否、再利用・中断再開・重複送信防止を検証します。バイナリのテストを含めるには`.[atmosphere]`をインストールしてください。

## 公式資料

- [EAC4データ・ライセンス・収録範囲](https://ads.atmosphere.copernicus.eu/datasets/cams-global-reanalysis-eac4?tab=overview)
- [変数名・単位・3時間解析・0.75度格子](https://confluence.ecmwf.int/spaces/CKB/pages/83395896/CAMS+Reanalysis+data+documentation)
- [ADS APIと手動での利用条件への同意](https://ads.atmosphere.copernicus.eu/how-to-api)
- [公式NetCDF ZIP要求例・取得範囲の制限](https://confluence.ecmwf.int/pages/viewpage.action?pageId=601307427)
