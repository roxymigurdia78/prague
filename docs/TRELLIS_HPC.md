# 画像をHPCへ送り、GLBを受け取る

## Windowsへの自動保存

手元のWindowsで `scripts/windows/receive_hpc_results.cmd` を開くか、pragueで次を実行します。

```powershell
python -X utf8 -u trellis_hpc.py watch-results
```

この受信プログラムを起動しておくと、準備済みジョブを30秒ごとに確認し、HPC側での完了後にGLB・高密度メッシュ・manifest・ログをWindowsへ受信します。
保存先は `C:\Users\Astra\Desktop\prague\outputs\hpc_jobs\ジョブ名\GLBs\` です。
HPCでの生成開始・停止・再生成は行わないので、生成は引き続きご自身のSSH画面から実行できます。
受信済みの結果は再取得しません。接続が一時的に切れた場合は、次の確認時に再接続します。
受信プログラムは二重起動を防止します。

現在はWindowsのバックグラウンドで起動します。PC再起動後は `scripts/windows/receive_hpc_results.cmd` で再度起動してください。
Windowsを終了・スリープしている間は受信できません。HPC側の生成結果は残るので、後から受信できます。
バックグラウンド版の記録は `outputs\hpc_jobs\receiver.log` と `receiver_error.log`、PIDは `.receiver.pid` に保存します。

## CPUへのモデル退避で待機が続く場合（2026-10-31）

手動実行のログでは、形状・テクスチャのサンプリング終了後に
`decode_tex_slat -> tex_slat_decoder.cpu()` で同じスタックが繰り返し記録されました。
確認時のGPU使用率は0%、利用可能メモリは約89GBでした。
`Timeout (0:02:00)!` は診断用の定期スタック表示で、処理を終了するタイムアウトではありません。

`--keep-models-on-gpu` で、`pipeline.cuda()` より前に `pipeline.low_vram = False` を設定する専用コピーを作成できます。
CPUへのモデル退避を避ける回避策で、根本原因や完走はまだ確認できていません。メモリ使用量は増加します。
画質設定は1536、テクスチャ4096、最終約15万面、seedは実行時ランダムです。
HPCの共有コードは変更していません。

回避版は配置済みですが、まだ生成を開始していません。前の処理を停止した後、SSH画面で実行します。

```bash
bash /home/23rd067/kuro/jobs/trellis_b56343089cc69aacb10b8c2c/run_interactive.sh
```

前の処理でCtrl+Cが効かない場合は、手元のpragueのPowerShellから、対象ジョブだけを停止できます。

```powershell
python run.py hpc cancel --job trellis_a09ac9af837a1ea7aa560025
```

回避版が完了したら、受け取りは次のジョブ名です。

```powershell
python run.py hpc fetch --job trellis_b56343089cc69aacb10b8c2c
```

`src/streetlab/trellis_hpc.py` は、既存の `sao\mvl-skeleton\python\hpc_generate.py` のSSH/SCP接続処理を再利用します。
接続先は `hpc-cloudflare`、認証は既存の `~/.config/hpc-portal/hpc-ssh.json` とSSH設定です。
APIキーはHPCへ転送しません。HPC側の既存環境・共有コードも変更しません。

今回確認したHPC側のコードは、貼り付けられたPythonコードと同じ内容でした。
高解像度設定 `1536_cascade` とseed 42が残っていました。
現在の実行用コピーはseedを固定せず、実行のたびに乱数で選びます。選んだ値はログとHPCの生成結果manifestに記録します。
共有コードは変更せず、ジョブごとの `batch_trellis_job.py` に変更を反映します。

## ご自身でSSH接続して実行する

`C:\Users\Astra\Desktop\prague\scripts\windows\connect_hpc.cmd` をダブルクリックします。
これはWindowsの `ssh hpc-cloudflare` を、既存のCloudflare認証情報を環境変数に渡して起動する接続用ファイルです。
生成コマンドは自動で実行しません。ターミナルに出るHPC側のプロンプトでご自身で入力します。
認証情報は表示・コピーしません。

接続後、次を入力すると接続先を確認できます。

```bash
hostname
whoami
pwd
```

今回の画像と実行ファイルは、次の場所に配置済みです。生成はまだ開始していません。

```bash
bash /home/23rd067/kuro/jobs/trellis_a09ac9af837a1ea7aa560025/run_interactive.sh
```

進行状況はそのターミナルに表示され、HPC側の `log.txt` にも残ります。
2分ごとに停止箇所の診断用スタックも記録するため、長く待機する場合に原因を調べられます。
完了後 `exit` でSSH接続を終了します。pragueのローカルターミナルで次を実行するとGLBを受け取れます。

```powershell
python run.py hpc fetch --job trellis_a09ac9af837a1ea7aa560025
```

前のseed 42での実行は大まかな形状推定後に待機が続いたため停止しました。そのログと記録は別ジョブとして保管しています。
新しい実行も同じ箇所で待機する場合は、ログの診断結果を使って調べてください。

## 実行

以下は自動送信・実行・受け取りをまとめて行う方法です。ご自身でSSHから実行する場合は、上の手順を使ってください。

pragueで次を実行します。`--image` には画像編集後の `trellis_input.png` を指定してください。
写真を改めて画像生成APIへ送る処理はありません。

```powershell
python run.py hpc run --image "画像の絶対パス" --normal-map --save-highres
```

1. 画像をHPCの専用ジョブフォルダに転送。
2. ApptainerでGPUを使い、既存の `trellis2_env.sh` と `batch_trellis_glb.py` を実行。
3. 30秒おきに完了を確認。
4. GLB・HPCログ・生成結果一覧を `C:\Users\Astra\Desktop\prague\outputs\hpc_jobs\ジョブ名\` に取得。
5. GLBの形式、面数、材質・テクスチャ、要求したノーマルマップの存在を確認。

初期値は4096のテクスチャと、約150,000面への軽量化です。
seedは省略時ランダムです。固定したい場合だけ `--seed 43` のように指定します。
`--normal-map` は高密度形状の凹凸をノーマルマップに焼きます。
`--save-highres` は軽量化前の形状も `object_high.glb` として保存します。この高密度GLBは形状のみで、表示用の材質付きGLBは `object.glb` です。
`--flip-green` は指定していません。
1536は生成時の空間解像度で、出力画像のピクセル数・最終ポリゴン数とは別の設定です。

## 中断と再開

同じ画像内容・同じ設定は同じジョブ名になり、再実行しても新しい生成を開始しません。
新しい試行を明示的に作る場合は `--attempt 2` のように番号を変えます。前の処理が終わっていることを確認してください。
開始要求の応答が不明な場合も、勝手に再送しません。HPC側には二重起動を防ぐロックを置きます。
PC側を閉じてもHPCの処理は継続します。

状況の確認：

```powershell
python run.py hpc status --job trellis_表示された識別子
```

完了した結果の受け取り：

```powershell
python run.py hpc fetch --job trellis_表示された識別子
```

`batch_trellis_glb.py` は画像別の例外を捕捉して継続するため、終了コード0だけでは成功と見なしません。
取得したmanifestの画像別成功状態とGLBの内容も確認します。
本スクリプトは生成まで行います。Blenderでの外観確認、実寸合わせ、SPZ内のドアの削除・差し替えは別工程です。
