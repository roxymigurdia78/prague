# 太陽・空を更新する関数

`src/streetlab/sun_sky.py` はMultiple Scatteringの9項目を設定・読み取り・保存・復元する部品です。`set_sun()`で場所・日時からの太陽位置計算もつながっています。
importだけではシーンを変更しません。既存の配置検出ループにも自動接続していません。
実際に `set_sky()` を呼んだときだけ、そのシーンのWorldを設定します。

## 場所・日時から太陽を設定する

```python
import sys
sys.path.insert(0, r'C:\Users\Astra\Desktop\prague\src')
from streetlab.sun_sky import set_sun
from streetlab.north_orientation import get_north

# Blender内で実行。初回は北をランダムに決め、次回からは同じ北を使う。
result = set_sun(50.08804, 14.42076, '2026-07-15', '08:00+02:00', altitude=202)
print(result['solar_position'])
print(get_north())

# 時刻だけ変える。北・大気の倍率・露出・既存のライトは維持。
set_sun(50.08804, 14.42076, '2026-07-15', '20:00+02:00')
```

太陽の高度・地理上の方位を既存の`solar_math.solar_position()`で計算し、保存した北に合わせてSky Textureの高度と回転へ反映します。天文計算に外部ライブラリは使いません。追加のSUNライトは作成しません。

時刻には上の例のようにUTCオフセットを含めるか、`timezone_name='Europe/Prague'`を指定してください。タイムゾーンを省略した現地時刻は受け付けず、実行するPCの時刻設定から推測しません。オフセットはその日の夏時間に合った値が必要です。IANAタイムゾーン名を使う場合、Blender側にもzoneinfoのデータが必要です。重複する現地時刻は`fold=0`または`fold=1`で指定します。

## 世界ごとに北を一度だけ抽選する

北は実地図から取得した方位ではなく、生成された架空の街の基準方向です。街のメッシュ自体は回しません。

- 1つのBlender Sceneを1つの街として扱います。初回の`set_sun()`または`ensure_north()`で0〜360度の北を抽選します。保存した値がある限り再抽選しません。
- 北の角度・seed・角度の規約をSceneのカスタムプロパティへ保存します。`.blend`を通常保存すれば、閉じて開き直しても同じ北です。
- 次回以降の街の準備処理では、`street_relight.blend`を作る際に北を保存し、同じ成果物フォルダへ`north.json`も保存します。以前のチェックポイントから準備をやり直しても、そのJSONを読み直します。照明のない準備シーンでは北の記録だけを行います。
- 初回の抽選を再現したい場合は`set_sun(..., north_seed=42)`または`ensure_north(seed=42)`を使えます。`0`も有効なseedです。北が既にあれば、seedを指定しても保存済みの北が優先されます。
- 任意の角度へ明示的に変更するときは`set_north(角度)`を使い、その後`set_sun()`を呼び直します。準備フォルダのJSONを併用している場合、SceneとJSONが食い違うと停止するため、意図した変更として両方の記録を管理してください。

内部の北角は「ワールド+Xから+Z軸まわりに反時計回り」の度数です。北が90度なら+Yが北、+Xが東になります。太陽の地理方位は北から時計回りなので、Skyノードへの回転は`(90 - 北角 + 太陽方位) % 360`で変換します。`result['sun_direction_world']`は太陽のある方向への単位ベクトルです。

```python
from streetlab.north_orientation import ensure_north, get_north, save_north

# 準備済みのフォルダなら、保存済みJSONを使って北を引き継ぐことも可能。
ensure_north(path=r'C:\Users\Astra\Desktop\prague\outputs\自分の世界\north.json')
```

従来の9項目の設定JSONには北や日時は含めません。北は`.blend`と専用の`north.json`で保持します。既存の成果物はこの実装だけでは自動で開いたり上書きしたりしません。

## 9項目を直接変更する

BlenderのPython ConsoleやBlender内のスクリプトから使います。

```python
import sys
sys.path.insert(0, r'C:\Users\Astra\Desktop\prague\src')
from streetlab.sun_sky import set_sky, get_sky_settings, save_sky_settings, load_sky_settings

# 9項目を一括設定。角度はすべて度、altitudeはメートル。
set_sky(
    sun_disc=True,
    sun_size=0.545,
    sun_intensity=1.0,
    sun_elevation=15.0,
    sun_rotation=0.0,
    altitude=100.0,
    air=1.0,
    aerosols=1.0,
    ozone=1.0,
)

# 後から一部だけ更新。書かなかった項目は現在値を維持。
set_sky(sun_elevation=8.0, aerosols=1.5)
print(get_sky_settings().to_dict())

# 値を保存し、同じ条件を再現。
save_sky_settings(r'C:\Users\Astra\Desktop\prague\outputs\sky_trial.json')
load_sky_settings(r'C:\Users\Astra\Desktop\prague\outputs\sky_trial.json')
```

同じシーンで繰り返し呼んでも、WorldやSkyノードは増殖しません。
Blenderのノード画面で手動変更した値も次の部分更新へ引き継ぎます。
別のシーンが同じWorldを共有している場合、更新するシーン用にコピーして分離します。
設定したWorldはBlenderファイルを通常保存すれば残ります。関数がファイルを自動保存することはありません。

## ループで使う例

```python
import bpy
from streetlab.sun_sky import set_sky, save_sky_settings

for i, haze in enumerate([0.5, 1.0, 1.5, 2.0]):
    set_sky(aerosols=haze)
    # ここで画像を撮影し、後から評価結果に応じて次の値を決める。
    # 保存先のディレクトリは先に用意しておく。
    save_sky_settings(fr'C:\Users\Astra\Desktop\prague\outputs\sky_{i:02d}.json')
```

これは更新の例で、写真やVLMから次の値を推定する処理は含みません。
`config/sun_sky.json` に同じ9項目の初期設定があります。写真からの実測値ではありません。

## 単位と動作

- `sun_size`, `sun_elevation`, `sun_rotation`: 度。Blender内部のラジアンへ関数内で変換。
- `sun_elevation`: -90〜90度。`sun_rotation` は0〜360度へ正規化。`set_sky()`への直接指定はSkyノードの規約、`set_sun()`は保存済みの北を使って地理方位から変換します。
- `altitude`: 0〜100000メートル。観測地点の海抜高度。
- `sun_intensity`, `air`, `aerosols`, `ozone`: 非負の倍率。許容範囲内でも実際の地球の大気に適切とは限りません。
- `sun_disc`: bool。OFFでも大きさと強度の設定値は保持するので、ONに戻した際に使えます。
- 直射光はSky Textureの太陽円盤が担当。追加のSUNライトは作成しません。Sun Discの照明にはCyclesを使います。
- WorldはSky → Background（強度1）→ World Output。メッシュ・材質・窓や器具の目印・既存ライト・カメラを変更しません。
- `set_sky()` または `set_sun()` で管理対象の空を初めて作ると、露出を `0` EV に設定します。以降の更新は、その時点の露出を維持します。
- 不正な値、NaN、単位の違う設定ファイルを拒否し、検証に失敗した更新ではシーンを変更しません。

写真のメタデータ取得と、大気データからBlenderの倍率への換算は別工程です。

Blender内の検証は`tests/blender/verify_sun_position_blender.py`です。使い捨てのシーンで保存・再読み込み・時刻更新を検証し、Cyclesで太陽方向に円盤が描画されることも確認します。
