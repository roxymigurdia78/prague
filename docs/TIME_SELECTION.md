# 日の出・日没に合わせて時刻を選ぶ

```powershell
python -X utf8 run.py time --date 2026-07-15 --period 夕方
python -X utf8 run.py time --season 春 --period 朝
python -X utf8 run.py time --month 5 --period 早朝 --seed 42
python -X utf8 run.py time --date 2026-07-15 --time 18:30
python -X utf8 run.py time
```

初期地点はプラハ（緯度50.08804、経度14.42076、Europe/Prague）。`config/time_selection.json` で変更できます。座標はOpen-Meteo Geocodingで取得したGeoNamesの代表地点です（CC BY 4.0、https://www.geonames.org/、https://open-meteo.com/en/docs/geocoding-api）。APIへの追加問い合わせはありません。標高202mは地点情報として記録しますが、日の出・日没計算では観測者高度・地形による地平線補正は行いません。

別の地点をCLIで指定するときは、緯度・経度・タイムゾーンをまとめて渡してください。

```powershell
python run.py time --latitude 52.52437 --longitude 13.41053 --timezone Europe/Berlin --date 2026-07-15 --period 夕方
```

## 区分の初期ルール

| 区分 | 判定 |
| --- | --- |
| 明け方 / dawn | 太陽中心が-6度以上、日の出基準より下で、太陽が昇る側 |
| 早朝 / early_morning | 日の出後の最大60分 |
| 朝 / morning | 早朝の後、昼の前 |
| 昼 / noon | 太陽の南中前後、各最大90分 |
| 午後 / afternoon | 昼の後、夕方の前 |
| 夕方 / evening | 日没前の最大60分 |
| 夜 / night | 日没基準より下。日没直後の薄明も含む。明け方・深夜は別区分 |
| 深夜 / midnight | 太陽中心が-6度未満で、現地22〜翌4時 |

日の短い季節は早朝・昼・夕方の窓を短縮し、日中の区分の重なりを避けます。具体的には日の出〜南中、南中〜日没それぞれの3分の1以内に各窓を制限します。窓幅と深夜の時計時刻は設定JSONから変更できます。これらの区分名はプロジェクトの運用ルールであり、天文学上の厳密な分類ではありません。

分単位でその日に存在する時刻を分類し、指定区分内から一様に抽選します。区分未指定なら、その日に選択できる区分から等確率で1つ選びます。白夜・極夜などでは存在しない区分を除き、明示指定された区分がなければ理由を返して停止します。

## 日付・時刻・夏時間

日付が未指定なら既存の `resolve_date()` を使用します。季節や月の条件を引き継ぎ、半球は地点の緯度から選びます。既定年は `config/date_selection.json` の2026年です。日付と時刻の抽選は同じ保存seedで再現できます。`--time` で指定された時刻は抽選しません。時刻と時間帯を両方指定し、矛盾する場合は停止します。

IANAタイムゾーンを使い、日付ごとの夏時間を反映します。夏時間開始で消える時刻は拒否し、終了で2回ある時刻は `--fold 0`（先）か `--fold 1`（後）を要求します。ランダム選択はUTC上で列挙してから現地時刻へ変換するため、存在しない時刻を選びません。Windows向けにタイムゾーンデータの `tzdata` を依存関係へ追加しています。天文計算はPython標準の数式実装です。

## 関数・JSONで接続

```python
from streetlab.time_selection import resolve_time

location = {"latitude": 50.08804, "longitude": 14.42076, "timezone": "Europe/Prague"}
result = resolve_time(location, "2026-07-15", {"period": "夕方"}, seed=42)
# 第2引数には resolve_date() が返した辞書も渡せます。
```

`--input` には次のような入力JSONを渡せます。会話をJSONへ抽出する処理は含みません。

```json
{
  "location": {"latitude": 50.08804, "longitude": 14.42076, "timezone": "Europe/Prague"},
  "month": 5,
  "period": "夕方"
}
```

locationには地名検索結果から選んだ候補1件を渡してください。候補一覧から勝手に先頭を選ぶ処理はありません。結果JSONと入力JSONは別形式です。

出力は `outputs/time_choices/`。採用日時（現地時刻・UTC・UTCオフセット）、地理情報、日の出・南中・日没・薄明、太陽高度・方位、seed、条件と仮定を保存します。既存ファイルは上書きしません。Blenderや既存ワールドは変更しません。

## 計算と限界

NOAA/Meeusの近似式で計算します。NREL SPAの完全実装ではなく、SPAの高精度保証を主張しません。対応年は1800〜2100。太陽高度は屈折補正前の太陽中心の幾何学的高度、方位は真北0度から時計回りです。Blender内の北方向との対応は別工程で、方位をSkyの回転へ自動適用しません。

日の出・日没は太陽中心の高度-0.833度（標準的な屈折と円盤半径の合算補正）、市民薄明は-6度を基準に探索します。実際の大気状態、地形、建物、観測者の高さで見える時刻は変わります。5分間隔の探索と0.1秒幅までの数値的な絞り込みを使いますが、この数値幅は観測との誤差保証を意味しません。極付近でごく短時間だけ閾値をまたぐ場合などは、探索間隔による見逃しもあり得ます。

- NOAA式・限界：https://gml.noaa.gov/grad/solcalc/calcdetails.html
- NOAA簡易計算式：https://gml.noaa.gov/grad/solcalc/solareqns.PDF
- 独立した検証例：NREL/TP-560-34302 Table A5.1（https://docs.nlr.gov/docs/fy08osti/34302.pdf）
- タイムゾーン：https://docs.python.org/3/library/zoneinfo.html
