# 地名から座標を取得

無料・非商用のOpen-Meteo Geocoding APIを利用します。APIキーは不要です。公開URLは秘密情報ではないため、`.env` ではなく `config/geocoding.json` に保存しています。

プロジェクトのルートで実行します。

```powershell
python -X utf8 run.py location "Prague" --country CZ
python -X utf8 run.py location "Berlin" --country DE
python -X utf8 run.py location "京都" --country JP
```

返却JSONの `results` に候補を保持します。国、行政区域、緯度・経度、海抜高度 `elevation`（メートル）、タイムゾーンなど、APIが返した情報を確認できます。欠けた標高やタイムゾーンを推測して埋める処理はありません。日本語で見つからない場合は英語や現地表記も試してください。

同名都市を勝手に選ばず、候補を返します。`--country` は2文字の国コードです。都市の座標・標高は観測地点の近似値であり、生成された街並みの実際の所在地を示すものではありません。Blenderの空やシーンを変更する処理は含みません。

検索結果、取得日時、検索URL、出典は `outputs/geocoding/cache.sqlite3` に保存されます。同じ条件は保存結果を返し、再びAPIを呼びません。最新情報を再取得したい場合は、別の `--cache` ファイルを明示してください。公開された地名だけを送信し、会話全文やAPIキーは送りません。

ローカルの制限は2秒以上の間隔、毎分500回・毎時4,500回・直近24時間9,000回・UTC暦月270,000回です。失敗したリクエストも数え、自動再試行はしません。同じDBを使うプロセス間で呼び出しを直列化します。他のアプリや同じIPの利用分は把握できないので、共有IPでは公式側の上限に先に達する場合があります。キャッシュDBの削除・変更で回数記録も変わるため、制限の回避に使用しないでください。

出典：Open-Meteo Geocoding API / 地名データ：GeoNames / CC BY 4.0。結果JSONにも出典を付けています。成果物に使用する場合は出典を引き継いでください。

- 仕様：https://open-meteo.com/en/docs/geocoding-api
- 利用規約：https://open-meteo.com/en/terms
- GeoNames：https://www.geonames.org/
- ライセンス：https://creativecommons.org/licenses/by/4.0/
