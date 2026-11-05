# 自動テスト

プロジェクトのルートから `python run.py test` を実行します。APIやHPCに接続せず、生成・配置解析・設定更新などを確認します。一時ファイルは `scratch/tests/` に作成します。

`tests/` はGitで保持するテストコードです。削除してよい試し書き・結果は `scratch/` に置いてください。

Blender内で動かす検証コードは `tests/blender/` にあります。たとえばPowerShellで次のように実行します。

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --background --factory-startup --python-exit-code 1 --python tests/blender/sky_blender_checks.py
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --background --factory-startup --python-exit-code 1 --python tests/blender/verify_sun_sky_blender.py -- scratch/blender_sun_sky
```

Blenderの実行ファイルは自分のインストール先に合わせてください。`verify_inventory_scene.py` など既存の成果物を検証するスクリプトは、対象フォルダを `--` の後に渡して使います。
