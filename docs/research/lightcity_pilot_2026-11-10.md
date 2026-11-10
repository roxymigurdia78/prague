# LightCityを使った照明評価の試運転

確認日: 2026-11-10。以下の実験は提案であり、有効性を示す実験結果ではない。

## 先に区別すること

「光の方向を推定できる」「指定した時間帯に見える」「光が自然に見える」は別の能力。
方向が当たることだけで、自然さを順位付けする評価器として使えるとは結論しない。
物理的なレンダリングに異なる光を与えた8枚にも、自動的に自然さの優劣は付かない。
LightCityのshadingやnormalは自然さの1〜5点の教師ではない。

## 公式配布先と入手方法

- [論文・プロジェクト](https://23wjj.github.io/LightCity/)
- [公式GitHub](https://github.com/23wjj/LightCity)
- [公式配布先 whalejj/LightCity](https://huggingface.co/datasets/whalejj/LightCity/tree/main)
- [Intrinsicの説明](https://huggingface.co/datasets/whalejj/LightCity/blob/main/LightCity_Intrinsic/README.md)
- [Intrinsic testのZIP一覧](https://huggingface.co/datasets/whalejj/LightCity/tree/main/LightCity_Intrinsic/test)

Intrinsicは同じ視点ごとに8照明条件の画像を連続保存する。RGB、Albedo、Shading、Normal、Depthが説明されている。
`jsons_intrinsics`の説明はRGBと成分画像の対応付けであり、時刻・太陽方向が必ず含まれるとは書かれていない。
公開READMEの数値ファイル名を使い、RGBだけを数値順で並べる。成分画像を8枚の数に含めない。
最初に実物を並べ、同じ視点か確認する。欠番や番号の開始位置を確認する前に`番号//8`で機械的に分けない。

公式APIで確認したtestファイル容量:

| ファイル | バイト数 | 概算GiB |
|---|---:|---:|
| render_group_5.zip | 12,052,984,667 | 11.23 |
| render_group_6.zip | 9,536,889,978 | 8.88 |
| render_group_7.zip | 12,643,287,660 | 11.77 |

まず最小の`render_group_6.zip`だけ取得する方法がある。全体の一括取得は不要。
数十枚を使うだけでも通常の配布は大きいZIP単位。解凍後のサイズは未確認。
本セッションでは一覧のみ照会し、画像ZIPの取得・解凍は行っていない。

HPCで取得するときのコマンド（実行前に保存先の空き容量を確認する）:

```bash
cd ~/light_eval
.venv312/bin/hf download whalejj/LightCity \
  LightCity_Intrinsic/test/render_group_6.zip \
  --repo-type dataset --local-dir data/lightcity
```

`hf`がない場合は環境内のhuggingface_hubの有無を確認してから対応する。
大量取得を伴うため、ノートではまず一覧確認だけを1セルで行い、結果を見て次へ進む。
ブラウザを閉じても処理が継続する保証は、nohupだけでなくSlurmジョブの生存にも依存する。

## 最初の小さい実験

1. 同一視点8照明の組を1組だけ並べる。RGB、成分画像、JSONの構造を確認する。
2. 使える正解を決める。方向・日時が追跡できなければ、その項目の正解率は計算しない。
3. 視点を5組に増やす。画像ごとにあなたが「照明の自然さ」を盲検で採点する。
4. 同じ40枚について、学習前Qwenで「実写なし」と「条件の近い実写のお手本あり」を比較する。
5. 人の採点との順位一致、各視点内の順位、実写を入れ替えた場合の安定性を見る。

条件が異なる写真の太陽方位をCGの正解として与えない。日時・カメラ方角が不明な写真を、正確な方位や太陽高度の教師にしない。
同じ8照明を5視点で使っているなら、独立した照明40種類とは扱わない。
学習するときは同一視点の8枚を同じ分割にまとめ、未知の街への一般化を調べるには街ごと分離する。
ウィーンは最終テストとして残す。LightCityを予備実験に使っても、元のプラハ・ドイツ学習計画を自動的に変更しない。

### 実写を学習に使う次の比較

まずCGのみで学習する条件と、同じCGに実写教材を追加する条件を比較する。
実写にも画像を見た人の採点を付ける。元画像と露出・色調の変化を混ぜても、加工量から正解点数を決めない。
同じ写真から作った全候補は同じ分割にまとめる。
更新回数、学習率、CG教材などを記録し、データ量や学習時間の差も交絡として扱う。
「CG＋実写」と「CG＋実写＋人間評価」を比較するなら、前者の教師が何かを別途定義する必要がある。

## 光方向を測るコード

`scripts/research/lighting_direction_metrics.py`は、確認済みの正解方向と予測方向の角度差を計算する。
画像から方向を抽出するモデル、Qwen実行、LightCity固有メタデータの変換は含めない。
メタデータの実物と座標変換を確認してから接続する。

正解JSONLの例:

```json
{"image_id":"view01_light01","assessable":true,"frame":"blender_world_z_up","toward_light":true,"direction_xyz":[1,0,1],"source":"自分のレンダリング設定に記録した太陽方向"}
{"image_id":"view01_cloudy","assessable":false,"reason":"単一の主光源方向を正解として定義しない"}
```

予測JSONLの例:

```json
{"image_id":"view01_light01","frame":"blender_world_z_up","toward_light":true,"azimuth_deg":0,"elevation_deg":40}
```

- `direction_xyz`は表面から光源へ向かうベクトル。光が進む方向と逆向き。長さは正規化する。
- 角度表現は+Xを方位0度、+Yへ回して90度、XY面から+Zへ仰角を取る。地理的な北0度と同一とは限らない。
- カメラ座標の予測と世界座標の正解は、実際に回転変換してから比較する。文字列のframeだけ合わせても変換にはならない。
- Qwenが「左から」と答えただけでは3次元方向は決まらない。正確な角度に勝手に置き換えない。
- 不明と答える場合は、frameとtoward_lightを記入したままdirection_xyzをnullにする。
- 既知の正解があるのに予測が欠損・棄権したケースも分母に残し、coverageを併記する。
- 角度差を計算できた画像だけの平均は、同じ画像集合を使って条件間比較する。
- 15度・30度以内率は診断用の閾値であり、光の自然さを保証する基準ではない。

```bash
python scripts/research/lighting_direction_metrics.py \
  --truth truth.jsonl --predictions predictions.jsonl --output direction_metrics.json
```

同じフォルダへ結果を上書きしない。出力の親フォルダは先に用意する。
コードの幾何・欠損・座標不一致のテストはローカルで実行可能。実データの方向評価は未実施。

## 関連研究の比較

以下の「本研究への使い方」は提案・解釈。既存論文がQwenの採点能力や、実写教材の転移を保証するものではない。

| 研究 | 入力→出力・目的 | 本研究への使い方／違い | 一次資料 |
|---|---|---|---|
| Deep Outdoor Illumination Estimation, CVPR 2017 | 屋外LDR写真→照明パラメータ・HDR照明 | 実写から照明を得る根拠。任意の指定日時の最適照明を出すものではない | [論文](https://arxiv.org/abs/1611.06403) |
| All-Weather Deep Outdoor Lighting Estimation, CVPR 2019 | 晴天・曇天の写真→HDR屋外照明。空と物体のレンダリングを利用 | 光の見え方を物体側でも確かめる考え。Blenderの空モデルとパラメータは同一でない | [公式](https://lvsn.github.io/allweather/) |
| Deep Sky Modeling, CVPR 2019 | SUN360と校正済みHDR空データを使い、写真→HDR環境マップ | 異なる役割のデータの併用例。CG＋実写＋人間採点が有効だという証明ではない | [論文](https://arxiv.org/abs/1905.03897) |
| Self-supervised Outdoor Scene Relighting, ECCV 2020 | 実写集合から自己教師ありで成分分解・再照明 | 影・材質・照明を分ける関連研究。公開実装はTF1.12/Python3.6指定で、現在のvenvへ即導入する対象ではない | [公式コード](https://github.com/YeeU/relightingNet) |
| Time-conditioned Illumination, BMVC 2024 | 異なる照明の複数画像→形状・材質・時間依存照明 | 時間条件の関連研究。本研究は生成済み世界を対象にする想定 | [公式論文ページ](https://bmvc2024.org/proceedings/697/) |
| DiffusionLight, CVPR 2024 | 写真→生成した鏡面球→HDR環境マップ | 写真由来照明の比較候補。推定・生成値であり測定の正解ではない | [公式](https://diffusionlight.github.io/) |
| LightCity, ICCV 2025 | 同一都市・複数照明のRGBと成分画像 | 世界生成を待たずに照明差を試せる。主観的な自然さの点数は別途必要 | [公式](https://23wjj.github.io/LightCity/) |
| Physically Controllable Relighting of Photographs, SIGGRAPH 2025 | 写真から中間3D表現を作り、明示的光源で再照明 | 物理制御と見た目の両立の関連研究。公式ページは知財保護・ライセンス照会を案内しており即導入可能とは扱わない | [公式](https://yaksoy.github.io/PhysicalRelighting/) |

添付文の「2026年の光理解評価研究」は題名・著者・一次資料が特定されていないため、引用候補へ含めない。
太陽方向を固定するだけでは、過大露出や不適切な光強度などの不自然さは防げない。
日時・場所だけで天候や唯一の正解の見た目は決まらず、目標の天候・写り方と許容する探索範囲を記録する。
