"""Warm-start a separate LoRA experiment from the existing HPC notebook.

The original notebook, adapter, split, API registration and Windows model choice
are preserved. Run --check to validate the frozen input without using the GPU.
"""
import argparse
import ast
from datetime import datetime, timezone
import gc
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
from urllib.request import Request, urlopen


def write_json(path, value):
    pending = path.with_suffix(path.suffix + '.tmp')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    pending.replace(path)


def digest(path):
    value = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


class Experiment(ast.NodeTransformer):
    def __init__(self, assignments=None, warm_start=False):
        self.assignments = assignments or {}
        self.warm_start = warm_start
        self.replaced_warm_start = 0

    def visit_Assign(self, node):
        self.generic_visit(node)
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if len(names) == 1 and names[0] in self.assignments:
            node.value = ast.parse(self.assignments[names[0]], mode='eval').body
        if self.warm_start and names == ['setup']:
            if not isinstance(node.value, ast.Dict):
                raise ValueError('Notebook training configuration changed')
            node.value.keys.extend([ast.Constant('initial_adapter'), ast.Constant('initial_weights_sha256')])
            node.value.values.extend([ast.parse('str(INITIAL_ADAPTER)', mode='eval').body,
                                      ast.Name(id='INITIAL_DIGEST', ctx=ast.Load())])
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        if self.warm_start and isinstance(node.func, ast.Name) and node.func.id == 'get_peft_model':
            self.replaced_warm_start += 1
            return ast.copy_location(ast.parse(
                'PeftModel.from_pretrained(base_model, str(INITIAL_ADAPTER), '
                'is_trainable=True, local_files_only=True)', mode='eval').body, node)
        return node


def compile_cell(source, transformer=None):
    tree = ast.parse(source)
    if transformer:
        tree = transformer.visit(tree)
    return compile(ast.fix_missing_locations(tree), '<frozen lighting notebook>', 'exec')


def prepare(root, output, epochs, learning_rate, model_name):
    snapshot = output / 'reference_notebook.ipynb'
    if not snapshot.is_file():
        shutil.copy2(root / 'aachen_test2_pair_lora.ipynb', snapshot)
    notebook = json.loads(snapshot.read_text(encoding='utf-8'))
    cells = [''.join(cell['source']).strip() for cell in notebook['cells']
             if cell['cell_type'] == 'code' and ''.join(cell['source']).strip()]
    if len(cells) != 5 or any(marker not in cell.splitlines()[0]
                              for marker, cell in zip(('①', '②', '③', '④', '⑤'), cells)):
        raise ValueError('Notebook layout changed; review the runner before training')
    original = root / 'outputs/aachen_test2_pair_v1/A_all'
    adapter = original / 'adapter'
    namespace = {'__name__': '__lighting_experiment__'}
    changes = {'ACTION': repr('prepare'), 'EPOCHS': repr(epochs),
               'LEARNING_RATE': repr(learning_rate), 'OUTPUT': f'Path({str(output / "experiment")!r})'}
    exec(compile_cell(cells[0], Experiment(changes)), namespace)
    namespace['ACTION'] = 'A_all'
    exec(compile_cell(cells[1]), namespace)
    old = json.loads((original / 'run_config.json').read_text())
    checks = {'model': str(namespace['MODEL']), 'seed': namespace['SEED'],
              'split': namespace['train_ids'], 'eval': namespace['heldout_ids'],
              'pairs': namespace['fingerprint'](namespace['train_pairs']),
              'csv': digest(namespace['DATA'] / 'aachen_scores.csv'),
              'image_edge': namespace['LONG_EDGE'], 'accumulation': namespace['ACCUMULATION']}
    if any(old.get(key) != value for key, value in checks.items()):
        raise ValueError('Original training data or split changed; no GPU operations were started')
    question = next(ast.literal_eval(node.value) for node in ast.walk(ast.parse(cells[2]))
                    if isinstance(node, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == 'QUESTION' for target in node.targets))
    if question != old['prompt']:
        raise ValueError('Training question differs from the original model')
    namespace.update(INITIAL_ADAPTER=adapter, INITIAL_DIGEST=digest(adapter / 'adapter_model.safetensors'))
    registry = json.loads((root / 'lighting_api/models.json').read_text())
    existing = registry['models'].get(model_name)
    if existing and Path(existing['adapter']).expanduser().resolve() != output / 'experiment/A_all/adapter':
        raise ValueError('Model name already belongs to another experiment')
    config = checks | {'initial_adapter': str(adapter), 'initial_weights_sha256': namespace['INITIAL_DIGEST'],
                      'extra_epochs': epochs, 'learning_rate': learning_rate,
                      'notebook_sha256': digest(snapshot), 'model_name': model_name}
    config_path = output / 'experiment_config.json'
    if config_path.is_file() and json.loads(config_path.read_text()) != config:
        raise ValueError('Experiment conditions changed; use a different output')
    write_json(config_path, config)
    train = Experiment(warm_start=True)
    training_code = compile_cell(cells[3], train)
    if train.replaced_warm_start != 1:
        raise ValueError('Expected exactly one adapter initialization')
    evaluation = Experiment({'available': '{"a_all": INITIAL_ADAPTER, '
                             f'{model_name!r}: OUTPUT / "A_all" / "adapter"' + '}', 'models': '{}'})
    evaluation_code = compile_cell(cells[4], evaluation)
    print('VALIDATED', len(namespace['train_pairs']), 'training pairs; separate original-photo heldout', flush=True)
    return namespace, compile_cell(cells[2]), training_code, evaluation_code


def service_running(folder, stored):
    try:
        command = Path(f'/proc/{stored["pid"]}/cmdline').read_bytes().split(b'\0')
    except FileNotFoundError:
        return False
    if not command or command == [b'']:
        return False
    if str(folder / 'lighting_api.py').encode() not in command:
        raise RuntimeError('Stored service PID belongs to another program; no process was stopped')
    return True


def stop_service(folder, stored):
    if not service_running(folder, stored):
        return
    os.kill(stored['pid'], signal.SIGTERM)
    for _ in range(80):
        if not service_running(folder, stored):
            return
        time.sleep(.25)
    raise RuntimeError('API did not stop; training was not started')


def start_service(root, folder, port):
    with (folder / 'server.log').open('ab') as log:
        process = subprocess.Popen([str(root / '.venv312/bin/python'), '-u',
                                    str(folder / 'lighting_api.py'), '--port', str(port)],
                                   cwd=folder, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    write_json(folder / 'service.json', {'pid': process.pid, 'port': port})
    token = (folder / 'api.token').read_text().strip()
    for _ in range(40):
        if process.poll() is not None:
            raise RuntimeError('API restart failed; inspect lighting_api/server.log')
        try:
            request = Request(f'http://127.0.0.1:{port}/health', headers={'Authorization': 'Bearer ' + token})
            with urlopen(request, timeout=2) as response:
                if json.load(response).get('status') == 'ok':
                    return
        except OSError:
            pass
        time.sleep(.25)
    raise RuntimeError('API health check timed out after restart')


def register(root, model_name, adapter):
    path = root / 'lighting_api/models.json'
    registry = json.loads(path.read_text())
    value = {'base_model': str(root / 'models/qwen3.8-27b'), 'adapter': str(adapter), 'long_edge': 768}
    if model_name in registry['models'] and registry['models'][model_name] != value:
        raise ValueError('Model name is already registered to a different experiment')
    registry['models'][model_name] = value
    write_json(path, registry)


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--extra-epochs', type=int, default=1)
    parser.add_argument('--learning-rate', type=float, default=1e-5)
    parser.add_argument('--model-name', default='a_all_extra1')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    root = Path.home() / 'light_eval'
    output = args.output.expanduser().resolve()
    if (not output.is_relative_to(root / 'outputs') or output == root / 'outputs'
            or not 1 <= args.extra_epochs <= 3 or not 0 < args.learning_rate <= 1e-4
            or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', args.model_name)):
        parser.error('Check the experiment output, epochs, learning rate or model name')
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.session.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status_path = output / 'status.json'
        if status_path.is_file() and json.loads(status_path.read_text()).get('phase') == 'completed':
            print('Experiment already complete:', output)
            return
        namespace, load_code, train_code, evaluation_code = prepare(
            root, output, args.extra_epochs, args.learning_rate, args.model_name)
        if args.check:
            return
        def status(phase, **values):
            write_json(status_path, {'phase': phase, 'pid': os.getpid(),
                                    'updated': datetime.now(timezone.utc).isoformat(), **values})
        folder = root / 'lighting_api'
        stored = json.loads((folder / 'service.json').read_text())
        # Verify ownership before entering the section that restarts this service.
        service_running(folder, stored)
        paused = False
        try:
            status('pausing_api')
            stop_service(folder, stored)
            paused = True
            status('training', model_name=args.model_name)
            exec(load_code, namespace)
            exec(train_code, namespace)
            status('evaluating', model_name=args.model_name)
            trainer = namespace.pop('trainer', None)
            if trainer is not None:
                trainer.optimizer = None
                trainer.lr_scheduler = None
                del trainer
            if 'training_model' in namespace:
                namespace['base_model'] = namespace.pop('training_model').unload()
            gc.collect()
            namespace['torch'].cuda.empty_cache()
            namespace.update(ACTION='evaluate', EVAL_VIENNA_ONLY=False)
            exec(evaluation_code, namespace)
            adapter = namespace['OUTPUT'] / 'A_all/adapter'
            register(root, args.model_name, adapter)
            status('restoring_api', model_name=args.model_name, adapter=str(adapter),
                   evaluation=str(namespace['OUTPUT'] / 'pair_eval_summary.csv'),
                   note='Original a_all remains selected. Reused Vienna results are exploratory.')
        except BaseException as exc:
            status('failed', error=type(exc).__name__)
            raise
        finally:
            namespace.clear()
            gc.collect()
            if paused:
                try:
                    start_service(root, folder, stored['port'])
                except BaseException:
                    status('api_restart_failed', port=stored['port'])
                    raise
                current = json.loads(status_path.read_text())
                current['api_restored'] = True
                if current['phase'] == 'restoring_api':
                    current['phase'] = 'completed'
                write_json(status_path, current)
                print('Original lighting API restored on port', stored['port'], flush=True)


if __name__ == '__main__':
    main()
