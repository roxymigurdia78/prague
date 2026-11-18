"""Run a timed lighting search, then a separate warm-start training experiment."""
import argparse
import json
import os
from pathlib import Path
import shutil
import time

from streetlab.build_world import read, run_lock, save
from streetlab.lighting_search import run_search
from streetlab.paths import ROOT
from streetlab.trellis_hpc import remote_python, transport


def connection():
    module = transport()
    return module.HPC(module.HPC_JSON, module.HOST)


def remote(hpc, source, timeout=60):
    code, out, err = hpc.run(remote_python(source), timeout=timeout)
    if code:
        raise RuntimeError('HPC operation failed: ' + (out + err)[-1800:])
    print(out.strip(), flush=True)
    return out


def prepare(session, minutes):
    session.mkdir(parents=True, exist_ok=True)
    config_path = session / 'config.json'
    if config_path.is_file():
        print('Prepared session:', session)
        return
    hpc = connection()
    folder = hpc.home + '/light_eval/lighting_jobs'
    remote(hpc, f'from pathlib import Path; Path({folder!r}).mkdir(exist_ok=True)')
    hpc.put(ROOT / 'scripts/hpc', ['continue_lighting_training.py'], folder)
    config = {'build': str(ROOT / 'outputs/builds/20261109_234836_223354'),
              'output': str(ROOT / 'outputs/lighting_trials/a_all_loop_01'),
              'minutes': minutes, 'judge': 'a_all',
              'remote_python': hpc.home + '/light_eval/.venv312/bin/python',
              'remote_runner': folder + '/continue_lighting_training.py',
              'remote_output': hpc.home + '/light_eval/outputs/' + session.name,
              'extra_epochs': 1, 'learning_rate': 1e-5,
              'model_name': 'a_all_extra1_' + session.name.removeprefix('lighting_study_')}
    argv = training_arguments(config) + ['--check']
    checked = remote(hpc, f'import os; os.execv({config["remote_python"]!r}, {argv!r})', timeout=120)
    if 'VALIDATED' not in checked:
        raise RuntimeError('Training input validation did not finish')
    for name in ('state.json', 'summary.json'):
        shutil.copy2(Path(config['output']) / name, session / ('before_search_' + name))
    save(config_path, config)
    save(session / 'status.json', {'phase': 'prepared', 'minutes': minutes,
                                 'training': 'queued after successful lighting search'})
    print('Prepared session:', session, flush=True)


def training_arguments(config):
    return [config['remote_python'], '-u', config['remote_runner'], '--output', config['remote_output'],
            '--extra-epochs', str(config['extra_epochs']), '--learning-rate', str(config['learning_rate']),
            '--model-name', config['model_name']]


def launch_training(config):
    # This remote launch is claimed once, including across uncertain SSH replies.
    # No automatic retry can accidentally start a second training process.
    return remote(connection(), f'''
from pathlib import Path
import json, os, subprocess, time
root=Path({config['remote_output']!r})
claim=root/'launch.json'
if claim.is_file():
 data=json.loads(claim.read_text())
 status=root/'status.json'
 complete=status.is_file() and json.loads(status.read_text()).get('phase')=='completed'
 pid=data.get('pid')
 command=Path(f'/proc/{{pid}}/cmdline')
 running=command.is_file() and {config['remote_runner']!r}.encode() in command.read_bytes().split(b'\\0')
 if not complete and not running: raise RuntimeError('Prior launch is not running; inspect training.log before resuming')
 print('EXISTING_TRAINING_LAUNCH',json.dumps(data))
else:
 fd=os.open(claim,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as stream: json.dump({{'phase':'launching'}},stream)
 with (root/'training.log').open('ab') as log:
  process=subprocess.Popen({training_arguments(config)!r},cwd=root,stdin=subprocess.DEVNULL,
                           stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 data={{'pid':process.pid,'phase':'launched'}}
 pending=claim.with_suffix('.tmp'); pending.write_text(json.dumps(data));pending.replace(claim)
 time.sleep(.5)
 if process.poll() not in (None,0): raise RuntimeError('Training runner exited; inspect training.log')
 print('TRAINING_LAUNCHED',json.dumps(data))
''')


def run(session):
    config = read(session / 'config.json')
    with run_lock(session):
        status = read(session / 'status.json')
        if status['phase'] == 'training_launched':
            print('This session has already launched training. Use --status.')
            return
        try:
            if status['phase'] != 'search_complete':
                started = status.get('search_started', time.time())
                remaining = max(1., config['minutes'] * 60 - (time.time() - started)) / 60
                save(session / 'status.json', {'phase': 'search_running', 'search_started': started,
                                             'pid': os.getpid(), 'minutes': config['minutes']})
                result = run_search(config['build'], output=config['output'], minutes=remaining, model=config['judge'])
                save(session / 'status.json', {'phase': 'search_complete', 'search_started': started,
                                             'search_finished': time.time(), 'search_output': str(result)})
            launch = launch_training(config)
            finished_search = read(session / 'status.json').get('search_output', config['output'])
            save(session / 'status.json', {'phase': 'training_launched', 'launched': time.time(),
                                         'remote_output': config['remote_output'], 'launch': launch.strip(),
                                         'model_name': config['model_name'], 'search_output': finished_search})
        except BaseException as exc:
            current = read(session / 'status.json')
            current.update(error=type(exc).__name__, failed_at=time.time())
            save(session / 'status.json', current)
            raise


def show_status(session):
    print(json.dumps(read(session / 'status.json'), ensure_ascii=False, indent=2))
    config = read(session / 'config.json')
    remote(connection(), f'''
from pathlib import Path
import json
root=Path({config['remote_output']!r})
p=root/'status.json'
print('HPC_STATUS',p.read_text() if p.is_file() else 'Waiting for lighting search to finish')
log=root/'training.log'
if log.is_file():
 with log.open('rb') as stream:
  stream.seek(max(0,log.stat().st_size-2200))
  print(stream.read().decode('utf-8',errors='replace'))
''')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--minutes', type=float, default=120)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--prepare', action='store_true')
    actions.add_argument('--run', action='store_true')
    actions.add_argument('--status', action='store_true')
    args = parser.parse_args()
    if not 0 < args.minutes <= 240:
        parser.error('Choose between 0 and 240 minutes')
    session = args.session.resolve()
    if not session.is_relative_to(ROOT / 'outputs/lighting_sessions'):
        parser.error('Session must be inside outputs/lighting_sessions')
    if args.prepare:
        prepare(session, args.minutes)
    elif args.run:
        run(session)
    else:
        show_status(session)


if __name__ == '__main__':
    main()
