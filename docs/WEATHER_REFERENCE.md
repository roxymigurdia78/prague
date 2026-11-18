# 過去の気象を参照する

描画する月日・現地時刻に対応する気象を取得します。**既定の参照年は2025年**です。描画年や既存の日付選択設定は変更しません。例えば描画日時2026-07-15 18:30なら、2025-07-15 18:30に近い気象を参照します。

```powershell
python -X utf8 run.py weather --date 2026-07-15 --time 18:30
python -X utf8 run.py weather --from-time "outputs/time_choices/保存したファイル.json"
```

初期地点は `config/time_selection.json` のプラハです。別の地点は `--latitude`、`--longitude`、`--timezone` をまとめて指定してください。`--from-time` の場合は保存済みの地点と日時を使用し、日時・地点の上書きは受け付けません。

## 必要なときだけ20年分

```powershell
# 取得計画だけ確認（API呼び出し・ファイル作成なし）
python run.py weather --date 2026-07-15 --time 18:30 --past-years 20 --dry-run

# 2006〜2025年の「7月15日」の気象を保存。採用する年は引き続き2025年。
python run.py weather --date 2026-07-15 --time 18:30 --past-years 20

# 取得した年から1年をランダムに採用する場合
python run.py weather --date 2026-07-15 --time 18:30 --past-years 20 --sample-year --seed 42

# 参照年を直接指定
python run.py weather --date 2026-07-15 --time 18:30 --reference-year 2020
```

20年間の全日・全時刻を取得するモードではありません。各年の同じ月日を対象にします。年ごとの対象日の全時間値を保存し、その中から指定した現地時刻に最も近い、気温・気圧・湿度のそろった1時間値を選びます。各項目を別々の年から抽選することはありません。利用可能な完全値が1時間以内になければ停止します。補間は行わず、時刻差も記録します。

2月29日や夏時間切り替えで存在しない参照日時は、単年モードでは停止し、複数年モードでは理由付きで除外します。除外により既定参照年が含まれない場合、採用年はnullで保存します。別年を使うには `--reference-year` または `--sample-year` で明示してください。描画時刻自体が夏時間終了で重複する場合は `--fold 0` または `--fold 1` が必要です。

## 取得項目と出典

Open-Meteo Historical Weather APIを使用し、比較する年でモデルが変わらないよう `models=era5` に固定します。

| 変数 | 内容 | 単位 |
| --- | --- | --- |
| temperature_2m | 地上2mの気温 | °C |
| surface_pressure | 地表気圧（海面更正気圧とは別） | hPa |
| relative_humidity_2m | 地上2mの相対湿度 | % |
| cloud_cover | 雲量 | % |
| precipitation | 直前1時間の降水量 | mm |
| weather_code | 天気分類の参考 | WMOコード |

街角での実測そのものではなく、ERA5に基づく再解析・API処理後の値です。APIが返した格子座標・標高、取得URL・取得日時・単位・参照日時・実際のサンプル時刻・出典・seedを記録します。

これは気象の取得部品です。晴れ／曇りの描画設定、候補の天気による絞り込み、Air/Aerosols/Ozoneへの変換、Blenderへの適用は行いません。歴史上の雲量と、ユーザーが希望する天気を同一扱いしません。

結果は `outputs/weather_reference/` に保存します。既存ファイルは上書きしません。URLと既定年は `config/weather_reference.json` に置き、APIキーや `.env` は使用しません。

## 通信量と利用条件

無料APIの非商用利用向けです。公開研究などの非商用利用に該当することと、出典表示を確認して使用してください。APIサーバーの利用条件とCC BYのデータライセンスは別に守る必要があります。

- 1地点・6変数・前後を含む3つのUTC日だけを1回で要求します。日時の重複・日付変更線に対応するためUTCで取得し、対象の現地日付へ切り出します。
- 単年モードはキャッシュがなければ1要求。20年モードは最大20要求です。10変数超・14日超など、複数回分として数えられる要求はこのHTTP部品では拒否します。
- 既存の地名検索と同じ `outputs/geocoding/cache.sqlite3` でキャッシュ・呼び出し記録を共有します。旧形式の地名キャッシュも再問い合わせなしで利用できます。
- 呼び出し間隔は2秒以上。ローカル上限は毎分500・毎時4500・直近24時間9000・UTC暦月270000回。失敗も数え、自動再試行はしません。
- 他アプリや共有IPの呼び出しは数えられません。DBを削除・分離して制限を回避しないでください。バッチ途中で失敗しても、それまでの成功応答はキャッシュに残るため再実行で再利用できます。
- JSONにもOpen-Meteo/ECMWF ERA5の出典とCC BY 4.0を記録します。配布・発表時には出典と加工内容の表示を引き継いでください。

仕様：https://open-meteo.com/en/docs/historical-weather-api

規約：https://open-meteo.com/en/terms

回数の数え方：https://open-meteo.com/en/pricing

## CAMS EAC4で取得するもの

気圧・気温・湿度は上記の取得経路です。次の大気成分は別の`atmosphere`コマンドで取得します。[使い方](ATMOSPHERE_REFERENCE.md)

- Total aerosol optical depth at 550 nm：エアロゾルの光学的厚さ。Aerosols倍率への変換・検証の根拠。
- Total column ozone：大気全体のオゾン量。単位変換後、Ozone倍率への近似の根拠。

利用時はADSアカウント・Personal Access Tokenと対象データの利用条件への手動同意が必要です。3時間間隔・0.75度格子の地域代表値であり、取得値をそのままBlenderへ代入することはできません。`python run.py atmosphere --from-weather "このコマンドが保存したJSON"`で気象側の採用年を引き継げます。

データ説明：https://ads.atmosphere.copernicus.eu/datasets/cams-global-reanalysis-eac4?tab=overview

公式取得手順：https://ads.atmosphere.copernicus.eu/how-to-api

## Pythonから呼ぶ

```python
from streetlab.weather_reference import fetch_weather, plan_weather

location = {"latitude": 50.08804, "longitude": 14.42076, "timezone": "Europe/Prague"}
plan = plan_weather(location, "2026-07-15T18:30:00+02:00", past_years=20)  # 通信なし
result = fetch_weather(location, "2026-07-15T18:30:00+02:00")             # 既定2025年
```
