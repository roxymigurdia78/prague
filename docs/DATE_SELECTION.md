# 季節・月から日付を選ぶ

会話の抽出処理を待たず、日付選択だけを使える部品です。AIへの接続、自由文の解釈、時刻選択、太陽位置計算、Blenderへの反映は含みません。

```powershell
python -X utf8 run.py date --season 春
python -X utf8 run.py date --month 5
python -X utf8 run.py date
python -X utf8 run.py date --season 冬 --seed 42
python -X utf8 run.py date --date 2026-05-10
```

指定がない場合は四季を等確率で1つ選び、その季節の有効な日を等確率で選びます。「春」なら春の範囲内、「5月」なら5月内で選びます。実在しない日付や矛盾した指定をランダムな日付に置き換えることはありません。

区分と既定年は `config/date_selection.json` で変更できます。初期値は年2026・北半球、春3〜5月、夏6〜8月、秋9〜11月、冬12・1・2月です。北半球の気象学的区分を初期ルールとして採用しています。

https://www.ncei.noaa.gov/news/meteorological-versus-astronomical-seasons

冬も指定した暦年の1・2・12月から選びます。前年12月から始まる連続した冬シーズンという意味ではありません。南半球は `--hemisphere south` で6か月ずらします。場所から半球を判断する処理はまだ接続していません。熱帯などではこの四季区分が気候を代表しないため、プロジェクトの仮定として扱ってください。

結果は `outputs/date_choices/` に日時付きJSONとして保存します。`--output` で保存先を指定でき、既存ファイルを上書きしません。採用日付、seed、元の指定、仮定した年と半球、抽選方法を記録します。指定のないseedは毎回生成します。同じ条件・設定・seedなら同じ日付を再現できます。保存した日付自体を再利用すれば再抽選は不要です。

会話からJSONを作る処理は後から接続できます。今は次の小さな入力形式だけを決めています。

```python
from streetlab.date_selection import resolve_date

resolve_date({"season": "spring"})
resolve_date({"month": 5})
resolve_date({})  # 季節からランダム
resolve_date({"date": "2026-05-10"})
```

使用できる入力キーは `season`, `month`, `day`, `year`, `date`, `hemisphere` です。月・日・年は整数、日付は `YYYY-MM-DD` です。`season` は日本語と英語（秋は `autumn`/`fall`）を受け付けます。未指定項目は省略またはnullにできます。結果JSONは入力条件とは別の保存用形式です。

ファイル入力は `python run.py date --input 条件.json` です。CLIで同じキーを指定した場合はCLIを優先します。入力JSON内の他のキーと矛盾する指定が残る場合は停止します。
