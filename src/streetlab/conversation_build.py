"""Local conversation JSON -> existing HPC extraction -> the complete world pipeline."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import sqlite3

from streetlab.paths import ROOT
from streetlab import conversation,build_world,world


def prepare_build(directory,source_file,*,marble_model=None,capture=None):
    """Pin one resumable build to one conversation, preserving its chosen conditions."""
    handoff_file=directory/'build_handoff.json'
    if handoff_file.exists():
        handoff=conversation.read_json(handoff_file)
        folder=Path(handoff['build_dir'])
        if handoff.get('conversation_hash')!=directory.name or not (folder/'build.json').is_file():
            raise ValueError('会話と一括実行の接続記録を確認してください: '+str(handoff_file))
        if marble_model is not None:
            saved=build_world.read(folder/'build.json')['config']['model']
            if saved!=marble_model:
                raise ValueError('同じ会話のモデルは保存済みです。別の世界を作る場合は会話文を変更してください。')
        return folder
    location=conversation.read_json(directory/'location.json')
    if location.get('source')=='config_default':
        raise ValueError('会話に国名と地名を一つ書いてください。既定のプラハでは生成を開始しません。')
    if location['location'].get('elevation') is None:
        raise ValueError('会話の地名から標高を取得できませんでした。地名を具体的にしてください。')
    chosen=conversation.read_json(directory/'time_selection.json')
    when=datetime.fromisoformat(chosen['local_datetime'])
    config=build_world.read(ROOT/'config/build_world.json')
    selected_model=marble_model or config['model']
    world_dir=directory/'world'
    original=world_dir/'generate_request.json'
    if original.exists():
        saved=conversation.read_json(original)
        if marble_model is not None and saved['model']!=marble_model:
            raise ValueError('生成済みの会話のモデルを変更できません。元のモデルで再開してください。')
        selected_model=saved['model']
        prompt=(directory/'world_prompt.txt').read_text(encoding='utf-8-sig').strip()
        if saved['world_prompt']['text_prompt'].strip()!=prompt:
            raise ValueError('生成済みのプロンプトと会話のプロンプトが一致しません。元の記録を保持して停止しました。')
    config.update(model=selected_model,world_dir=str(world_dir),
        prompt_source=str(directory/'world_prompt.txt'),conversation_dir=str(directory),
        conversation_source=str(source_file),date=when.date().isoformat(),
        time=when.time().isoformat(),datetime_from_prompt=False)
    if capture is not None:
        build_world.capture_mode(config,capture)
    parent=ROOT/'outputs/builds';parent.mkdir(parents=True,exist_ok=True)
    folder=parent/(datetime.now(world.JST).strftime('%Y%m%d_%H%M%S_%f')+'_conversation_'+directory.name[:12])
    folder.mkdir()
    # Seed the existing pipeline with the exact HPC-selected location and time;
    # it must not re-extract conditions from the neutral English Marble prompt.
    for name in ('conversation.json','extraction.json','backend.json','location.json',
                 'date_selection.json','time_selection.json','world_prompt.txt'):
        shutil.copy2(directory/name,folder/name)
    build_world.save(folder/'build.json',{'schema':'streetlab/world-build/v1','status':'created','config':config})
    build_world.save(handoff_file,{'schema':'streetlab/conversation-build/v1',
        'conversation_hash':directory.name,'build_dir':str(folder),'world_dir':str(world_dir)})
    return folder


def main(argv=None):
    parser=argparse.ArgumentParser(description='Windows上の会話JSONからHPC・Marble・GLB・清掃・検出・初期Blenderまで一括実行')
    parser.add_argument('input',nargs='?',type=Path,default=ROOT/'会話.json')
    parser.add_argument('--prompt-only',action='store_true',help='HPCで会話を整理し、プロンプト・地点・日時の保存まで。World生成は行わない')
    parser.add_argument('--dry-run',action='store_true',help='JSONの形式確認だけ。APIも保存も行わない')
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--hpc-model',default=conversation.DEFAULT_MODEL)
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--model',choices=world.GENERATION_CREDITS)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--with-capture',action='store_true',help='道路探索と本撮影も追加')
    mode.add_argument('--initialize-only',action='store_true',help='本撮影を省き、初期Blenderと撮影位置の土台まで')
    args=parser.parse_args(argv)
    source=args.input.resolve()
    data=conversation.read_input(source)
    messages=conversation.normalize_conversation(data)
    if args.dry_run:
        config=build_world.read(ROOT/'config/build_world.json')
        capture=True if args.with_capture else False if args.initialize_only else config.get('capture_enabled',False)
        print(json.dumps({'input':str(source),'messages':len(messages),'api_called':False,
            'output_saved':False,'capture_enabled':capture},ensure_ascii=False,indent=2))
        return 0
    print('会話JSON:',source,flush=True)
    status=conversation.process(data,output=ROOT/'outputs/conversations',seed=args.seed,
        env_file=args.env_file,model=args.hpc_model,generate=False,
        marble_model=args.model or build_world.read(ROOT/'config/build_world.json')['model'])
    directory=Path(status['run_dir'])
    if status['status']=='needs_review':
        labels={'world_description':'街の説明','season':'季節','time_of_day':'時間帯','place':'地名'}
        missing='・'.join(labels.get(item,item) for item in status.get('missing',[]))
        print('会話に追記してください:',missing or status.get('reason','条件の確認'),flush=True)
        print('会話を書き直して、同じコマンドを実行してください。',flush=True)
        return 2
    print('HPCが生成したプロンプト:',directory/'world_prompt.txt',flush=True)
    location=conversation.read_json(directory/'location.json')
    if location.get('source')=='config_default':
        raise ValueError('会話に国名と地名を一つ書いてください。')
    chosen=conversation.read_json(directory/'time_selection.json')
    print('地点・日時:',location['location']['name'],
        str(location['location'].get('elevation'))+'m',chosen['local_datetime'],flush=True)
    if args.prompt_only:
        print('プロンプトの準備まで完了しました。World生成は行っていません。',flush=True)
        return 0
    requested_capture=True if args.with_capture else False if args.initialize_only else None
    # This second lock covers the durable handoff and complete build. The
    # extractor's own .processing lock remains compatible with the Mac receiver.
    with build_world.run_lock(directory):
        folder=prepare_build(directory,source,marble_model=args.model,capture=requested_capture)
        with build_world.run_lock(folder):
            if requested_capture is not None:
                job=build_world.read(folder/'build.json')
                build_world.capture_mode(job['config'],requested_capture)
                build_world.save(folder/'build.json',job)
            print('一括実行の保存先:',folder,flush=True)
            with build_world.pinned_world_key():
                build_world.execute(folder)
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (ValueError,RuntimeError,OSError,sqlite3.Error) as error:
        print('停止:',str(error),flush=True)
        raise SystemExit(1)
    except Exception as error:
        # Provider exception bodies can contain endpoints or credentials.
        print('停止:',type(error).__name__,'で停止しました。同じコマンドで再開できます。',flush=True)
        raise SystemExit(1)
