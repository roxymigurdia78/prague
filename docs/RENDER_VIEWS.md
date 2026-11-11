# 固定視点で撮影する

`render_views()` は、開いているBlenderシーンを指定の設定で撮影し、PNGと `settings.json` を同じ `shot-...` フォルダに保存します。戻り値は `settings.json` のパスです。

既定ではプラハ旧市街（北緯50.087°、東経14.421°）の2026年7月15日12時（現地夏時間、UTC+02:00）、露出0 EV、ホワイトバランス補正なし、768×576、Cycles 16サンプルで撮影します。保存先の既定値は `C:\relight\renders` です。既存の北があれば使い、未設定の場合は毎回同じseedで北を決めます。

BlenderのPythonコンソールからの例です。

```python
import sys
sys.path.insert(0, r"C:\Users\Astra\Desktop\prague\src")
from streetlab.render_views import render_views
render_views({"露出": 0.5, "色温度": 5200, "sky": {"aerosols": 1.3}}, r"C:\relight\renders")
```

`色温度` は撮影側のホワイトバランスの基準白色点（K）です。空のパラメータは `sky` に渡します。`sun` に `{"date": "2026-07-15", "time": "18:00+02:00"}` を渡すと時刻を変更できます。冬の日付では現地時刻に合うUTCオフセットを指定してください。`timezone_name` とオフセットなしの時刻を指定する場合、BlenderのPythonから `tzdata` を使える環境が必要です。`sun=None` を渡すと現在の照明を使い、`sky` の `sun_elevation` と `sun_rotation` を直接指定できます。

アクティブなカメラ1台を使います。アクティブカメラがなく、シーンにカメラが1台あればそれを使います。現行の `street_mapped.blend` はカメラを持たないため、同じフォルダの `round_00/views.json` に記録されたスポーン付近の `r00_v00` を使います。後から `camera_names=["Street A", "Street B"]` のように渡すと、登録したカメラで順番に撮影できます。設定は撮影用の一時シーンに適用します。

`settings.json` には太陽位置、反映後の空と撮影の設定、各カメラの位置・向き・レンズ、画像名が入ります。同じ視点で朝夕や設定候補を比べられます。

PowerShellから起動するときは、Blenderに `.blend` と撮影スクリプトを渡します。`--settings` で上の辞書と同じ形式のJSONファイル、`--output` で保存先を指定できます。

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --background "街.blend" --python-exit-code 1 --python "C:\Users\Astra\Desktop\prague\src\streetlab\render_views.py" -- --output "C:\relight\renders"
```

## 評価ループで画像を片付ける

ループ全体を `temporary_render_batch()` で囲みます。必要な画像やJSONは `with` を抜ける前に保存先へコピーします。このブロックの終了時に専用フォルダを削除します。評価処理が例外で終了した場合も同じです。

```python
from streetlab.render_views import render_views, temporary_render_batch

with temporary_render_batch() as work_dir:
    for haze in (0.8, 1.0, 1.3):
        manifest = render_views({"sky": {"aerosols": haze}}, work_dir)
        # manifest と同じフォルダの画像をここで評価する。
    # 採用結果を残す場合は、このブロック内で専用フォルダの外へ保存する。
```

## 次の段階の視点選び

最初の候補はスポーン付近の道路上に置きます。進行方向・逆方向に加え、壁面や窓も写る斜め方向を含めます。道路上の2〜3地点で各3方向、計6〜9枚を試写するところから始め、見たい場所が入っていなければ追加します。遠方のぼけや崩れは候補画像を同じ照明で試写して確認し、採用した視点を以降の比較で固定します。
