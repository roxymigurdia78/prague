"""Run the project from a checkout without installing it."""
from pathlib import Path
import runpy
import sys
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

COMMANDS = {
    "build": ("build_world", "プロンプトからGLB・清掃・地点/大気・窓/街灯検出・初期Blenderまで一括実行"),
    "conversation-build": ("conversation_build", "Windowsの会話.jsonからHPCでプロンプトを作り、初期Blenderまで一括実行"),
    "roads": ("roads.explore_world", "HPCで道路を認識し、分岐探索・歩行経路・撮影計画を作成"),
    "conversation": ("conversation", "会話JSONをHPCで抽出（世界・季節・時間帯・地名）し、地点・日時・Blender条件へ接続／受信"),
    "atmosphere": ("atmosphere_reference", "CAMS EAC4のAOD550・オゾン全量を取得"),
    "weather": ("weather_reference", "既定2025年の気象を取得・過去20年にも対応"),
    "time": ("time_selection", "日の出・日没に合わせて時刻を選択"),
    "date": ("date_selection", "季節・月から日付を選択"),
    "location": ("geocoding", "地名から座標・標高・タイムゾーンを検索"),
    "world": ("world", "Marbleの世界を生成・取得"),
    "credits": ("world_api", "World Labsの残高を確認"),
    "prepare": ("prepare_world", "GLBの空・破片を清掃"),
    "capture": ("capture_world", "清掃・暗所化済みの街をCAMSの空と道路視点で撮影"),
    "map": ("scene_inventory", "窓・器具の位置を記録"),
    "sky-cleanup": ("sky_cleanup", "空の領域を検出・分離"),
    "sky-polish": ("sky_cleanup_polish", "残った空・破片を清掃"),
    "images": ("generate_material_variants", "小物の画像を生成・編集"),
    "trellis": ("trellis_watch", "入力画像のフォルダを監視"),
    "hpc": ("trellis_hpc", "HPCのジョブを操作"),
    "export-splats": ("export_splats", "従来のSPZ/PLY書き出し"),
}


def main():
    if len(sys.argv) == 1 or sys.argv[1] in {"-h", "--help"}:
        print("使い方: python run.py <コマンド> [引数]\n")
        for name, (_, description) in COMMANDS.items():
            print(f"  {name:15} {description}")
        print("  test            APIを呼ばずに自動テストを実行")
        print("\n各処理の引数: python run.py <コマンド> --help")
        return 0
    command = sys.argv[1]
    if command == "test":
        if len(sys.argv) > 2:
            print("使い方: python run.py test", file=sys.stderr)
            return 2
        output = ROOT / "scratch" / "tests"
        output.mkdir(parents=True, exist_ok=True)
        # Keep generic tempfile usage in the same disposable area.
        import tempfile
        tempfile.tempdir = str(output)
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if command not in COMMANDS:
        print(f"不明なコマンド: {command}（python run.py --help を参照）", file=sys.stderr)
        return 2
    if command in {"credits", "export-splats"} and len(sys.argv) > 2:
        print(f"使い方: python run.py {command}\n{COMMANDS[command][1]}")
        return 0 if sys.argv[2:] in [["--help"], ["-h"]] else 2
    sys.argv = [f"{Path(__file__).name} {command}", *sys.argv[2:]]
    runpy.run_module("streetlab." + COMMANDS[command][0], run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
