"""Run the existing world, cleanup, location, atmosphere and preview tools together."""
import argparse
import hashlib
from contextlib import contextmanager
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import subprocess

from streetlab.paths import ROOT, CODE_DIR
from streetlab import world, conversation, prepare_world, atmosphere_reference, weather_reference
from streetlab.sky_cleanup import find_blender
from streetlab.solar_math import get_zone
from streetlab.time_selection import _local_time
from streetlab.world_api import load_api_key


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


@contextmanager
def run_lock(folder):
    connection = sqlite3.connect(folder / 'run_lock.sqlite3', timeout=0)
    try:
        connection.execute('BEGIN EXCLUSIVE')
        yield
    finally:
        connection.rollback()
        connection.close()


def select_run(resume=False, run_dir=None, prompt_text=None):
    if run_dir:
        folder = Path(run_dir).resolve()
        if resume and not (folder / 'build.json').is_file():
            raise ValueError('再開できるbuild.jsonがありません。')
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    parent = ROOT / 'outputs' / 'builds'
    jobs = sorted(parent.glob('*/build.json'))
    if jobs and (resume or read(jobs[-1]).get('status') != 'complete'):
        previous_prompt=jobs[-1].parent/'world_prompt.txt'
        changed=(not resume and prompt_text is not None and previous_prompt.is_file()
            and previous_prompt.read_text(encoding='utf-8-sig').strip()!=prompt_text.strip())
        if not changed:
            return jobs[-1].parent
        print('プロンプトが変わったため、前の世界を残して新しい保存先を作ります。',flush=True)
    if resume:
        raise ValueError('再開できる一括実行の記録がありません。')
    parent.mkdir(parents=True, exist_ok=True)
    folder = parent / datetime.now(world.JST).strftime('%Y%m%d_%H%M%S_%f')
    folder.mkdir()
    return folder


def remaining_world_cost(world_dir, model):
    # A recorded submission is polled rather than submitted again, even if the
    # connection failed after POST. world.operation enforces its pending marker.
    generation_started = any((world_dir / name).exists() for name in (
        'generate_operation.json', 'generate_submission_pending.json', 'world.json'))
    export_started = any((world_dir / name).exists() for name in (
        'mesh_operation.json', 'mesh_submission_pending.json', 'mesh_export.json'))
    return (0 if generation_started else world.GENERATION_CREDITS[model][1]) + (0 if export_started else 3500)


def valid_local_glb(path):
    try:
        with path.open('rb') as file:
            magic,version,length=struct.unpack('<4sII',file.read(12))
        return magic==b'glTF' and version==2 and length==path.stat().st_size
    except (OSError,struct.error):
        return False


def cleanup_state(source):
    matches = []
    for job in (ROOT / 'outputs' / 'sky_cleanup').glob('*/job.json'):
        if Path(read(job)['source']).resolve() == source.resolve():
            matches.append(job.parent)
    if not matches:
        return None, None
    checkpoint = max(matches, key=lambda p: p.name)
    state = checkpoint / 'preparation.json'
    polished = Path(read(state)['polished']) if state.is_file() else None
    return checkpoint, polished


def cleanup_complete(checkpoint, polished):
    if checkpoint is None or polished is None:
        return False
    state=checkpoint/'preparation.json'
    return (state.is_file() and read(state).get('status')=='complete'
        and all((polished/name).is_file() for name in (
            'street_relight.blend','street_relight.glb','relight_report.json','views.json')))


def result_exists(folder):
    result_file = folder / 'result.json'
    if not result_file.is_file():
        return False
    result = read(result_file)
    if not all(Path(result[key]).is_file() for key in ('dark_blend', 'dark_glb', 'initial_blend')):
        return False
    captured=result.get('capture_enabled',True)
    job_file=folder/'build.json'
    if job_file.is_file():
        config=read(job_file)['config']
        if captured!=config.get('capture_enabled',True):
            return False
        if config.get('inventory',{}).get('enabled') and (
                not result.get('inventory_file') or not Path(result['inventory_file']).is_file()
                or not result.get('inventory_blend') or not Path(result['inventory_blend']).is_file()):
            return False
    if not captured:
        return (result.get('render_settings') is None and not result.get('views')
            and Path(result.get('camera_plan_file',folder/'camera.json')).is_file())
    if not result.get('render_settings') or not Path(result['render_settings']).is_file():
        return False
    manifest = Path(result['render_settings'])
    views = read(manifest).get('views', [])
    return bool(views) and all((manifest.parent / view['image']).is_file() for view in views)


def capture_mode(config,enabled):
    config['capture_enabled']=enabled
    config.setdefault('roads',{})['enabled']=enabled


@contextmanager
def pinned_world_key():
    # Pin the launch credential in this process only. Editing .env for the next
    # city must not change the key between this world's generation and export.
    key=load_api_key()
    previous=os.environ.get('WORLD_LABS_API_KEY')
    if key:
        os.environ['WORLD_LABS_API_KEY']=key
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('WORLD_LABS_API_KEY',None)
        else:
            os.environ['WORLD_LABS_API_KEY']=previous


def run_inventory(folder,prepared,config):
    from streetlab import scene_inventory
    options=config.get('inventory',{})
    if not options.get('enabled'):
        return None
    with (prepared/'street_clean.blend').open('rb') as file:
        source_digest=hashlib.file_digest(file,'sha256').hexdigest()
    identity={'source_sha256':source_digest,'rounds':options.get('rounds',2),
        'views_per_position':options.get('views_per_position',8),'model':options.get('model',scene_inventory.DEFAULT_MODEL)}
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:16]
    destination=folder/'inventory'/digest
    destination.mkdir(parents=True,exist_ok=True)
    # Initialization adds per-world metadata to the dark .blend. Keep this
    # inspection copy immutable so resuming does not invalidate the detections.
    source=destination/'source.blend'
    if not source.exists():
        shutil.copy2(prepared/'street_relight.blend',source)
        shutil.copy2(prepared/'views.json',destination/'views.json')
    complete=(destination/'result.json').is_file() and (destination/'inventory.json').is_file() and (destination/'street_mapped.blend').is_file()
    if not complete:
        args=[str(source),'--continue-dir' if (destination/'job.json').is_file() else '--output',str(destination),
            '--rounds',str(identity['rounds']),'--views-per-position',str(identity['views_per_position']),
            '--model',identity['model']]
        scene_inventory.main(args)
    result=read(destination/'result.json')
    inventory=read(destination/'inventory.json')
    if result.get('lights_added')!=0 or result.get('materials_changed') is not False or inventory.get('lighting_assigned') is not False:
        raise ValueError('検出結果が位置記録のみになっていません。照明・材質は初期状態で変更できません。')
    return {**result,'inventory':str(destination/'inventory.json'),'blend':str(destination/'street_mapped.blend')}


def show_result(result):
    summary=result.get('cleanup_summary')
    if summary:
        from streetlab.cleanup_convergence import REASONS
        print('清掃の終了理由:',REASONS.get(summary.get('termination_reason'),summary.get('termination_reason')),
            '確認'+str(summary.get('reviews_completed'))+'回',flush=True)
    print('照明ゼロ:',result['dark_blend'],flush=True)
    print('大気を反映した初期状態:',result['initial_blend'],flush=True)
    if result.get('inventory_file'):
        print('窓・街灯・ランタンの検出結果:',result['inventory_file'],flush=True)
    if result.get('render_settings'):
        print('画像と設定:',result['render_settings'],flush=True)
    else:
        print('撮影は省略しました。カメラ位置の土台はcamera.jsonに保存しています。',flush=True)


def resolve_scene_time(config, location, folder):
    target=folder/'time_selection.json'
    if target.exists():
        return datetime.fromisoformat(read(target)['local_datetime'])
    extracted=read(folder/'extraction.json') if (folder/'extraction.json').exists() else {}
    date_input=extracted.get('date_constraints',{})
    time_input=extracted.get('time_constraints',{})
    use_prompt=(config.get('datetime_from_prompt',False) and not config.get('datetime_override',False)
        and (extracted.get('season') or date_input) and (extracted.get('time_of_day') or time_input))
    if use_prompt:
        date_input={**date_input,'season':extracted.get('season'),
                    'hemisphere':'north' if location['latitude']>=0 else 'south'}
        time_input={**time_input,'period':extracted.get('time_of_day')}
    else:
        date_input={'date':config['date']}
        time_input={'time':config['time']}
        if config.get('fold') is not None:time_input['fold']=config['fold']
    date_input={k:v for k,v in date_input.items() if v is not None}
    time_input={k:v for k,v in time_input.items() if v is not None}
    chosen_date=conversation.resolve_date(date_input,seed=42)
    chosen_time=conversation.resolve_time(location,chosen_date,time_input,seed=42)
    save(folder/'date_selection.json',chosen_date)
    save(target,chosen_time)
    return datetime.fromisoformat(chosen_time['local_datetime'])


def execute(folder):
    job_file = folder / 'build.json'
    job = read(job_file)
    config = job['config']
    world_dir = Path(config['world_dir'])
    if job.get('status') == 'complete' and result_exists(folder):
        print('保存済みの一括実行は完了しています。', flush=True)
        show_result(job['result'])
        return folder

    def stage(name, **changes):
        if name!='interrupted':
            job.pop('error_type',None)
        job.update(status=name, **changes)
        save(job_file, job)
        print(name, flush=True)

    try:
        stage('location')
        location_file = folder / 'location.json'
        if not location_file.exists():
            if config.get('place'):
                extraction = {'place_name': config['place'], 'place_query': config['place'],
                    'place_country': config.get('country'), 'place_relation': 'inspired_by',
                    'evidence': {'place_name': config['place']}}
            else:
                extraction_file = folder / 'extraction.json'
                if not extraction_file.exists():
                    extraction, backend = conversation.extract([{'role': 'user', 'content':
                        (folder / 'world_prompt.txt').read_text(encoding='utf-8')}],require_marble_scene=False)
                    save(extraction_file, extraction)
                    save(folder / 'backend.json', backend)
                extraction = read(extraction_file)
            location, record = conversation.resolve_location(extraction, None, folder)
            if location.get('elevation') is None:
                raise ValueError('地点の標高が取得できません。別の地名を--placeで指定してください。')
            save(location_file, {'schema': 'streetlab/conversation-location/v1', **record})
        location = read(location_file)['location']
        when = resolve_scene_time(config,location,folder)
        if config.get('weather_enabled') and not (folder/'weather.json').exists():
            stage('weather')
            save(folder/'weather.json',weather_reference.fetch_weather(location,when,
                reference_year=config['reference_year']))
        # Validate atmosphere coverage and licensing/dependencies before paid world generation.
        atmosphere_reference.plan_atmosphere(location, when, reference_year=config['reference_year'])
        atmosphere_file = folder / 'atmosphere.json'
        if not atmosphere_file.exists():
            stage('atmosphere')
            reference = atmosphere_reference.fetch_atmosphere(location, when,
                reference_year=config['reference_year'])
            save(atmosphere_file, reference)
        source = world_dir / 'street_textured.glb'
        if valid_local_glb(source):
            print('保存済みGLBを再利用します。World APIへの再照会・再生成は行いません。',flush=True)
        else:
            stage('world')
            key = load_api_key()
            if not key:
                raise ValueError('WORLD_LABS_API_KEYがありません。')
            needed = remaining_world_cost(world_dir, config['model'])
            balance = world.api(key, 'credits')['remaining_credits']
            if balance < needed:
                raise ValueError(f'GLBまでの残高が不足しています。必要{needed:,}、残高{balance:,.0f}クレジット。')
            previous = world.OUT
            try:
                world.main(['generate', '--run-dir', str(world_dir), '--prompt-file', str(folder / 'world_prompt.txt'),
                            '--model', config['model']])
                stage('mesh')
                world.main(['mesh', '--run-dir', str(world_dir)])
            finally:
                world.OUT = previous
            if not valid_local_glb(source):
                raise ValueError('テクスチャ付きGLBがないか、不完全です。mesh_export.jsonを確認してください。')
        stage('cleanup')
        checkpoint, polished = cleanup_state(source)
        if not cleanup_complete(checkpoint,polished):
            options = [str(source)]
            if checkpoint:
                options += ['--continue-dir', str(checkpoint)]
            polished = prepare_world.main(options)
        inventory=None
        if config.get('inventory',{}).get('enabled'):
            stage('detecting_windows_and_fixtures',prepared_dir=str(polished))
            inventory=run_inventory(folder,polished,config)
        road_plan=None
        road_config=config.get('roads',{})
        if config.get('capture_enabled',True) and road_config.get('enabled'):
            from streetlab.roads.explore_world import main as explore_roads
            import hashlib
            road_source=polished/'street_clean.blend'
            road_digest=hashlib.sha256(road_source.read_bytes()).hexdigest()
            road_folder=folder/'roads'/road_digest[:16]
            road_plan=road_folder/'panoramas/capture_plan.json'
            complete_roads=False
            if road_plan.is_file() and (road_folder/'world_map.json').is_file():
                plan=read(road_plan)
                complete_roads=(bool(plan.get('views'))
                    and all((road_plan.parent/v['image']).is_file() for v in plan['views'])
                    and (road_plan.parent/'review/findings.json').is_file())
            if not complete_roads:
                stage('road_recognition_and_camera_planning',prepared_dir=str(polished),road_run_dir=str(road_folder))
                options=[str(road_source),'--resume' if (road_folder/'run.json').exists() else '--output',str(road_folder)]
                for key in ('eye','step','spacing','rounds','stations','panoramas'):
                    options+=['--'+key,str(road_config[key])]
                explore_roads(options)
            stage('roads_complete',road_plan=str(road_plan),road_map=str(road_folder/'world_map.json'))
        stage('initializing_and_rendering' if config.get('capture_enabled',True) else 'initializing', prepared_dir=str(polished))
        request = {'prepared_dir': str(polished), 'location': location,
            'local_datetime': when.isoformat(), 'atmosphere_file': str(atmosphere_file),
            'output': str(folder), 'width': config['width'], 'height': config['height'],
            'samples': config['samples'],'exposure':config.get('exposure',-2.0),
            'view_transform':config.get('view_transform','AgX'),
            'capture_enabled':config.get('capture_enabled',True)}
        if inventory:
            request.update(inventory_file=inventory['inventory'],inventory_blend=inventory['blend'])
        if road_plan:
            request.update(capture_plan=str(road_plan),capture_source=str(polished/'street_clean.blend'),
                width=road_config['width'],height=road_config['height'])
        save(folder / 'initialize_request.json', request)
        if not result_exists(folder):
            with (folder / 'initialize.log').open('w', encoding='utf-8') as log:
                completed = subprocess.run([str(find_blender()), '--background', '--disable-autoexec',
                    '--factory-startup', '--python-exit-code', '1', '--python',
                    str(CODE_DIR / 'build_world_blender.py'), '--', str(folder / 'initialize_request.json')],
                    stdout=log, stderr=subprocess.STDOUT)
            if completed.returncode:
                raise RuntimeError('初期設定・撮影で停止しました。保存先のinitialize.logを確認してください。')
        if not result_exists(folder):
            raise RuntimeError('初期シーンまたは試写画像・設定がそろっていません。')
        result = read(folder / 'result.json')
        if checkpoint is None:
            checkpoint,_=cleanup_state(source)
        if checkpoint and (checkpoint/'preparation.json').is_file():
            cleanup_record=read(checkpoint/'preparation.json')
            result['cleanup_summary']={key:cleanup_record.get(key) for key in (
                'termination_reason','reviews_completed','output_mode','policy','final_review_skipped')}
            save(folder/'result.json',result)
        stage('complete', result=result)
        show_result(result)
        return folder
    except Exception as exc:
        # Do not log provider exception bodies, which can contain credentials.
        stage('interrupted', error_type=type(exc).__name__)
        if isinstance(exc, (ValueError, RuntimeError, atmosphere_reference.AtmosphereError)):
            message = re.sub(r'https?://\S+', '[URL]', str(exc))
            secret = load_api_key()
            if secret:
                message = message.replace(secret, '[API KEY]')
            raise RuntimeError(message) from None
        raise RuntimeError(f'{type(exc).__name__}で停止しました。同じコマンドまたは--resumeで再開できます。') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description='プロンプト→世界→GLB→清掃・消灯→地名・大気→窓/街灯の検出→初期Blenderまで一括実行')
    parser.add_argument('--prompt-file', type=Path)
    parser.add_argument('--place', help='地名を明示。省略時は既存HPC抽出でプロンプトから取得')
    parser.add_argument('--country', help='地名を明示するときの国コード（CZ等）')
    parser.add_argument('--date', help='描画日。省略時はconfig/build_world.json')
    parser.add_argument('--time', help='地点の現地時刻。省略時はconfig/build_world.json')
    parser.add_argument('--fold', type=int, choices=(0, 1))
    parser.add_argument('--model', choices=world.GENERATION_CREDITS)
    parser.add_argument('--world-dir', type=Path, help='既存世界を使う。新規の世界生成を行わない')
    parser.add_argument('--run-dir', type=Path, help='一括実行の保存先を指定')
    parser.add_argument('--resume', action='store_true', help='最新の一括実行を再開。新規生成しない')
    parser.add_argument('--dry-run', action='store_true', help='設定表示のみ。APIも保存も行わない')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--initialize-only',action='store_true',help='道路探索・本撮影を省き、初期Blenderとカメラ位置の土台まで保存')
    mode.add_argument('--with-capture',action='store_true',help='道路探索と本撮影も実行。初期状態だけの既存世界にも追加できる')
    args = parser.parse_args(argv)
    config = read(ROOT / 'config/build_world.json')
    requested_capture=False if args.initialize_only else True if args.with_capture else None
    if requested_capture is not None:
        capture_mode(config,requested_capture)
    for key in ('place', 'country', 'date', 'time', 'fold', 'model'):
        if getattr(args, key) is not None:
            config[key] = getattr(args, key)
    if args.date is not None or args.time is not None:
        config['datetime_override']=True
    prompt_file = (args.prompt_file or ROOT / config.pop('prompt_file')).resolve()
    if args.resume and any(getattr(args, key) is not None for key in (
            'prompt_file', 'place', 'country', 'date', 'time', 'fold', 'model', 'world_dir')):
        parser.error('--resumeでは保存済み条件を使います。条件の変更はできません。')
    if args.country and not args.place:
        parser.error('--countryは--placeと一緒に指定してください。')
    if args.world_dir:
        world_dir = args.world_dir.resolve()
        if not (world_dir / 'generate_request.json').is_file() or not any((world_dir / name).is_file()
                for name in ('world.json', 'generate_operation.json', 'generate_submission_pending.json')):
            parser.error('--world-dirには既存の生成記録が必要です。')
        original = read(world_dir / 'generate_request.json')
        config['model'] = original['model']
        text = original['world_prompt']['text_prompt']
    else:
        world_dir = None
        text = None
    if args.dry_run:
        if text is None and not args.resume:
            text = prompt_file.read_text(encoding='utf-8-sig').strip()
        date.fromisoformat(config['date'])
        print(json.dumps({'config': config, 'prompt_file': str(prompt_file),
            'reuse_world_dir': str(world_dir) if world_dir else None,
            'text_prompt': text, 'api_called': False}, ensure_ascii=False, indent=2))
        return
    if not args.resume and text is None:
        text=prompt_file.read_text(encoding='utf-8-sig').strip()
    folder = select_run(args.resume, args.run_dir,prompt_text=text)
    with run_lock(folder):
        if not (folder / 'build.json').exists():
            if text is None:
                text = prompt_file.read_text(encoding='utf-8-sig').strip()
            if not text:
                raise ValueError('プロンプトが空です。')
            date.fromisoformat(config['date'])
            config['world_dir'] = str(world_dir or folder / 'world')
            config['prompt_source'] = str(prompt_file)
            (folder / 'world_prompt.txt').write_text(text, encoding='utf-8')
            save(folder / 'build.json', {'schema': 'streetlab/world-build/v1', 'status': 'created', 'config': config})
        else:
            saved_job=read(folder/'build.json')
            saved = saved_job['config']
            if world_dir is not None and Path(saved['world_dir']).resolve() != world_dir:
                raise ValueError('別の世界の一括処理が途中です。--run-dirで別の新しい保存先を指定してください。')
            if requested_capture is not None:
                capture_mode(saved,requested_capture)
                save(folder/'build.json',saved_job)
            print('保存済みのプロンプト・地点・日時で再開します。', flush=True)
        print('一括実行の保存先:', folder, flush=True)
        with pinned_world_key():
            return execute(folder)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
        print('停止:', str(exc), flush=True)
        raise SystemExit(1)
