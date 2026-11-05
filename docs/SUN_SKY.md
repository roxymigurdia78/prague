# 太陽・空を更新する関数

`src/streetlab/sun_sky.py` はMultiple Scatteringの9項目を設定・読み取り・保存・復元する部品です。
importだけではシーンを変更しません。既存の配置検出ループにも自動接続していません。
実際に `set_sky()` を呼んだときだけ、そのシーンのWorldを設定します。

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
- `sun_elevation`: -90〜90度。`sun_rotation` は0〜360度へ正規化。方位はBlenderのSkyノードと同じ規約で、地理的な北との対応はまだ設定しません。
- `altitude`: 0〜100000メートル。観測地点の海抜高度。
- `sun_intensity`, `air`, `aerosols`, `ozone`: 非負の倍率。許容範囲内でも実際の地球の大気に適切とは限りません。
- `sun_disc`: bool。OFFでも大きさと強度の設定値は保持するので、ONに戻した際に使えます。
- 直射光はSky Textureの太陽円盤が担当。追加のSUNライトは作成しません。Sun Discの照明にはCyclesを使います。
- WorldはSky → Background（強度1）→ World Output。メッシュ・材質・窓や器具の目印・既存ライト・カメラを変更しません。
- レンダラーや露出も変更しません。既存ライトや他の設定を含めた明るさの管理は呼び出し側で行います。
- 不正な値、NaN、単位の違う設定ファイルを拒否し、検証に失敗した更新ではシーンを変更しません。

日時・緯度経度からの太陽位置計算、写真のメタデータ取得、大気パラメータの推定は後工程です。
