"""Plan and run a daylight comparison from saved world builds."""

import argparse
from collections import Counter
import csv
from html import escape
import math
import os
from pathlib import Path
import random
import secrets
import sqlite3
import subprocess

from streetlab.build_world import read, run_lock, save
from streetlab.paths import CODE_DIR
from streetlab.sky_cleanup import find_blender
from streetlab.time_selection import resolve_time


# One change from the baseline in each image. White balance has two controls.
CASES = (
    ('baseline', '基準', {}),
    ('exposure_low', '露出 −2 EV', {'exposure_delta': -2}),
    ('exposure_high', '露出 +2 EV', {'exposure_delta': 2}),
    ('temperature_low', 'ホワイトバランス 4000 K', {'temperature_k': 4000}),
    ('temperature_high', 'ホワイトバランス 8000 K', {'temperature_k': 8000}),
    ('tint_low', 'ホワイトバランス 色合い −10', {'tint_delta': -10}),
    ('tint_high', 'ホワイトバランス 色合い +10', {'tint_delta': 10}),
    ('contrast_low', 'コントラスト 弱', {'look': 'AgX - Medium Low Contrast'}),
    ('contrast_high', 'コントラスト 強', {'look': 'AgX - Medium High Contrast'}),
    ('sun_low', '太陽光 ×0.5', {'sun_multiplier': 0.5}),
    ('sun_high', '太陽光 ×2', {'sun_multiplier': 2}),
    ('world_low', '空からの光 ×0.5', {'world_multiplier': 0.5}),
    ('world_high', '空からの光 ×2', {'world_multiplier': 2}),
    ('aerosols_low', 'かすみ ×0.5', {'aerosols_multiplier': 0.5}),
    ('aerosols_high', 'かすみ ×3', {'aerosols_multiplier': 3}),
)


def mixed_cases(count, seed):
    """Cover each control's range, pairing its levels independently."""
    if count < 2:
        raise ValueError('--mixed は2以上の組み合わせ数を指定してください。')
    rng = random.Random(f'lighting-combinations:{seed}')
    fractions = [i / (count - 1) for i in range(count)]
    controls = {
        'exposure_delta': [round(-2 + 4 * f, 2) for f in fractions],
        'temperature_k': [round(4000 + 4000 * f) for f in fractions],
        'tint_delta': [round(-10 + 20 * f, 2) for f in fractions],
        'look': [('AgX - Medium Low Contrast', 'None', 'AgX - Medium High Contrast')[i % 3]
                 for i in range(count)],
        'sun_multiplier': [round(0.5 * 4**f, 3) for f in fractions],
        'world_multiplier': [round(0.5 * 4**f, 3) for f in fractions],
        'aerosols_multiplier': [round(0.5 * 6**f, 3) for f in fractions],
    }
    for levels in controls.values():
        rng.shuffle(levels)
    return (CASES[0], *(
        (f'mix_{i + 1:02}', f'組み合わせ {i + 1:02}',
         {key: levels[i] for key, levels in controls.items()})
        for i in range(count)
    ))


def world_plan(folder, label, yaw, period, rng):
    result = read(folder / 'result.json')
    source = Path(result['initial_blend']).resolve(strict=True)
    location = read(folder / 'location.json')['location']
    saved_time = read(folder / 'time_selection.json')
    constraints = {'period': period or saved_time['period']}
    if period is None and 'time' in saved_time['requested']:
        build_file = folder / 'build.json'
        config = read(build_file)['config'] if build_file.is_file() else {}
        extraction_file = folder / 'extraction.json'
        extracted_time = (read(extraction_file).get('time_constraints', {}).get('time')
                          if extraction_file.is_file() else None)
        # A default clock time used to initialize Blender only supplies its
        # period. An actual request for a clock time stays fixed.
        if (not config or config.get('conversation_dir') or config.get('datetime_override')
                or extracted_time == saved_time['requested']['time']):
            constraints.update(saved_time['requested'])
    when = resolve_time(location, saved_time['date'], constraints, seed=rng.getrandbits(64))
    if when['period'] in ('night', 'midnight'):
        raise ValueError(f'{label}: 夜間は昼の比較撮影の対象外です。')

    inventory = result.get('inventory_file')
    counts = Counter(obj['kind'] for obj in read(inventory)['objects']) if inventory else {}
    if not result.get('camera_plan_views'):
        raise ValueError(f'{label}: 初期カメラの位置が記録されていません。')
    camera = result['camera_plan_views'][0]
    return {
        'id': folder.name, 'label': label, 'source': str(source),
        'source_mtime_ns': source.stat().st_mtime_ns, 'source_bytes': source.stat().st_size,
        'time_selection': when, 'north_deg': rng.choice(range(0, 360, 45)),
        'camera': {'position': camera['position'], 'source_view': camera['camera'],
                   'yaw_deg': yaw % 360, 'pitch_deg': -3, 'lens_mm': 24},
        'inventory_file': inventory, 'inventory_counts': counts,
    }


def write_gallery(output, plan):
    rows = []
    sections = []
    for world in plan['worlds']:
        cards = []
        for case in plan['cases']:
            folder = output / world['id'] / case['id']
            record = read(folder / 'settings.json')
            paths = {name: (folder / file).relative_to(output).as_posix()
                     for name, file in (('image', 'view.png'), ('blend', 'scene.blend'),
                                        ('settings', 'settings.json'))}
            if any(not (output / path).is_file() or not (output / path).stat().st_size
                   for path in paths.values()):
                raise RuntimeError(f'撮影結果がそろっていません: {folder}')
            rows.append({'world': world['label'], 'case': case['id'], 'label': case['label'],
                         'local_datetime': record['local_datetime'],
                         'north_deg': record['north_deg'], **paths})
            label = escape(case['label'])
            image, blend, settings = (escape(paths[k], quote=True) for k in paths)
            cards.append(f'<article><a href="{image}"><img src="{image}" alt="{label}" loading="lazy"></a>'
                         f'<div>{label}<br><a href="{blend}">Blender</a> · '
                         f'<a href="{settings}">設定値</a></div></article>')
        when = escape(world['time_selection']['local_datetime'].replace('T', ' '))
        sections.append(f'<section><h2>{escape(world["label"])}</h2>'
                        f'<p>現地時刻 {when} · 北 {world["north_deg"]}°</p>'
                        f'<div class="grid">{"".join(cards)}</div></section>')
    with (output / 'index.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / 'index.html').write_text(f"""<!doctype html>
<html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>街並みの昼光比較</title>
<style>
:root{{color-scheme:dark;font-family:system-ui,sans-serif}}
body{{margin:2rem auto;padding:0 1rem;max-width:1280px}}
section{{margin:3rem 0}} a{{color:#9ed9f7}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:1rem}}
article{{background:#242629;border-radius:.5rem;overflow:hidden}}
article img{{width:100%;aspect-ratio:4/3;display:block}} article div{{padding:.6rem}}
</style>
<h1>街並みの昼光比較</h1><p><a href="index.csv">画像一覧 CSV</a> · <a href="capture_plan.json">撮影条件</a></p>
{"".join(sections)}
</html>
""", encoding='utf-8')


def blender_command(plan_path, wsl_blender):
    script = CODE_DIR / 'lighting_capture_blender.py'
    prefix = [str(find_blender())] if not wsl_blender else ['wsl.exe', '--exec', wsl_blender]
    if wsl_blender:
        script, plan_path = (
            subprocess.check_output(['wsl.exe', '--exec', 'wslpath', '-u', str(path)],
                                    encoding='utf-8').strip()
            for path in (script, plan_path)
        )
    return [*prefix, '--background', '--disable-autoexec', '--factory-startup',
            '--python-exit-code', '1', '--python', str(script), '--', str(plan_path)]


def capture(build_dirs, *, output=None, labels=None, yaws=None, seed=None, period=None,
            wsl_blender=None, mixed=None):
    folders = [Path(folder).resolve(strict=True) for folder in build_dirs]
    labels = labels or [None] * len(folders)
    yaws = yaws or [0] * len(folders)
    if not folders or len({folder.name.casefold() for folder in folders}) != len(folders):
        raise ValueError('世界フォルダ名は撮影内で重複できません。')
    if len(labels) != len(folders) or len(yaws) != len(folders) or not all(map(math.isfinite, yaws)):
        raise ValueError('世界ごとにラベルと有限な撮影方位を指定してください。')
    if output is None and len(folders) != 1:
        raise ValueError('複数の世界には --output を指定してください。')
    output = Path(output or folders[0] / 'lighting_capture').resolve()
    output.mkdir(parents=True, exist_ok=True)
    plan_path = output / 'capture_plan.json'
    with run_lock(output):
        previous = read(plan_path) if plan_path.exists() else None
        seed = seed if seed is not None else previous['seed'] if previous else secrets.randbits(64)
        rng = random.Random(seed)
        worlds = [world_plan(folder, label or read(folder / 'location.json')['location']['name'],
                             yaw, period, rng)
                  for folder, label, yaw in zip(folders, labels, yaws)]
        cases = mixed_cases(mixed, seed) if mixed is not None else CASES
        plan = {'schema': 'streetlab/lighting-capture/v1', 'seed': seed,
                'resolution': [1280, 960], 'samples': 48, 'worlds': worlds,
                'cases': [{'id': key, 'label': label, 'changes': changes}
                          for key, label, changes in cases]}
        if mixed is not None:
            plan['combination_count'] = mixed
        if previous is not None and (previous | {'cases': plan['cases']}) != plan:
            raise ValueError('保存済みの撮影条件と異なります。新しい --output を指定してください。')
        if previous != plan:
            save(plan_path, plan)
        print('昼光比較の保存先:', output, flush=True)
        command = blender_command(plan_path, wsl_blender or os.environ.get('STREETLAB_LIGHTING_WSL_BLENDER'))
        with (output / 'capture.log').open('a', encoding='utf-8') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                log.write(f'Blender exit code: {result.returncode} (0x{result.returncode % 2**32:08X})\n')
        if result.returncode:
            raise RuntimeError(f'比較撮影で停止しました。{output / "capture.log"} を確認してください。')
        write_gallery(output, plan)
    print('画像ギャラリー:', output / 'index.html', flush=True)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description='清掃済みの初期Blenderから昼光の比較画像を撮影')
    parser.add_argument('build_dirs', type=Path, nargs='+', help='result.jsonがある世界の保存先')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--label', action='append', help='街の表示名。複数なら世界と同じ順に指定')
    parser.add_argument('--yaw', type=float, action='append', help='撮影の向き。複数なら世界と同じ順に指定')
    parser.add_argument('--period', help='保存した時間帯を上書きし、その時間帯から時刻を抽選')
    parser.add_argument('--seed', type=int, help='北と時刻の抽選を再現する整数')
    parser.add_argument('--mixed', type=int, metavar='N', help='複数の照明設定を同時に変えたN通りを撮影')
    parser.add_argument('--wsl-blender', help='既定のWSL環境にあるBlenderのLinuxパス')
    args = parser.parse_args(argv)
    try:
        capture(args.build_dirs, output=args.output, labels=args.label, yaws=args.yaw,
                seed=args.seed, period=args.period, wsl_blender=args.wsl_blender, mixed=args.mixed)
    except (KeyError, ValueError, RuntimeError, OSError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'停止: {exc}\n')


if __name__ == '__main__':
    main()
