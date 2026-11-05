"""Use the existing SSH transport to inspect/run TRELLIS.2 on KambeHPC."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import time

from streetlab.generate_material_variants import ROOT

TRANSPORT = ROOT.parent / "sao" / "mvl-skeleton" / "python" / "hpc_generate.py"
LOCAL_JOBS = ROOT / "outputs" / "hpc_jobs"


def transport():
    spec = importlib.util.spec_from_file_location("existing_hpc_transport", TRANSPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.LOCAL_JOBS = LOCAL_JOBS
    # Read/poll/transfer may be repeated explicitly; never automatically repeat a launch.
    module.RETRIES = 1
    return module


def remote_python(source):
    encoded = base64.b64encode(source.encode()).decode()
    return "python3 -c " + shlex.quote("import base64; exec(base64.b64decode(" + repr(encoded) + "))")


def inspect(hpc):
    source = '''from pathlib import Path
import json, subprocess
root = Path.home() / "kuro"
print("HPC_ROOT", root)
env = root / "trellis2_env.sh"
print("ENV_EXISTS", env.is_file())
if env.is_file():
    for line in env.read_text().splitlines():
        if any(key in line for key in ("cd ", "activate", "ATTN_BACKEND", "PYTHONPATH", "CUDA", "python", "venv")):
            print("ENV", line)
for name in ("batch_trellis_glb.py", "normal_bake.py"):
    paths = list(root.glob(name)) + list(root.glob("*/" + name))
    for path in sorted(set(paths)):
        print("FILE", path)
        if name == "batch_trellis_glb.py":
            print(path.read_text())
gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader"], capture_output=True, text=True)
print("GPU", gpu.stdout.strip() or gpu.stderr.strip())
'''
    code, out, err = hpc.run(remote_python(source), timeout=60)
    print(out)
    if code:
        raise RuntimeError("HPC inspection failed: " + err[:300])


def inner_command(paths, settings):
    q = shlex.quote
    inner = (f"source {q(paths['env'])} && python -u {q(paths['job'] + '/bootstrap.py')} "
             f"--runner-code {q(paths['job'] + '/batch_trellis_job.py')} "
             f"--input-dir {q(paths['images'])} --output-dir {q(paths['glbs'])} "
             f"--texture-size {settings['texture_size']} --decimation-target {settings['decimation_target']}")
    for key, flag in (("normal_map", "--normal-map"), ("save_highres", "--save-highres")):
        if settings[key]:
            inner += " " + flag
    return inner


def generation_command(module, paths, settings):
    q = shlex.quote
    inner = inner_command(paths, settings)
    # DONE is written even if the container fails; no automatic relaunch after uncertain submission.
    runner = (f"apptainer exec --nv {q(module.SIF)} bash -c {q(inner)}; "
              f"code=$?; printf '%s\\n' \"$code\" > {q(paths['done'] + '.tmp')}; "
              f"mv {q(paths['done'] + '.tmp')} {q(paths['done'])}")
    return (f"cd {q(paths['job'])} && mkdir launch.lock && "
            f"{{ nohup setsid bash -c {q(runner)} > {q(paths['log'])} 2>&1 < /dev/null & echo $!; }}")


def job_runner(source, seed, keep_models_on_gpu=False):
    if source.count("seed=42,") != 1 or source.count("IMAGE_EXTS =") != 1:
        raise ValueError("HPCのコード形式が変わっています。共有コードは変更していません。")
    expression = str(seed) if isinstance(seed, int) else "int.from_bytes(os.urandom(4), 'little') & 0x7fffffff"
    declaration = f"GENERATION_SEED = {expression}\nprint('Generation seed:', GENERATION_SEED, flush=True)\n\nIMAGE_EXTS ="
    result = source.replace("IMAGE_EXTS =", declaration, 1).replace("seed=42,", "seed=GENERATION_SEED,", 1)
    result = result.replace('"input_dir": str(input_dir),',
                            '"seed": GENERATION_SEED,\n        "pipeline_type": "1536_cascade",\n        "input_dir": str(input_dir),', 1)
    if keep_models_on_gpu:
        if result.count("    pipeline.cuda()") != 1:
            raise ValueError("HPCコードのGPU初期化箇所を特定できません。")
        result = result.replace("    pipeline.cuda()",
                                "    pipeline.low_vram = False\n"
                                "    print('CPU offload disabled: keeping models on GPU', flush=True)\n"
                                "    pipeline.cuda()", 1)
        result = result.replace('"input_dir": str(input_dir),',
                                '"low_vram": False,\n        "input_dir": str(input_dir),', 1)
    compile(result, "batch_trellis_job.py", "exec")
    return result


def interactive_script(module, paths, settings):
    q = shlex.quote
    return ("#!/usr/bin/env bash\nset -uo pipefail\n"
            f"cd {q(paths['job'])}\n"
            "mkdir launch.lock || { echo 'This job has already been started. Use a new attempt to regenerate.'; exit 73; }\n"
            f"finish() {{ code=$?; trap - EXIT; printf '%s\\n' \"$code\" > {q(paths['done'] + '.tmp')}; "
            f"mv {q(paths['done'] + '.tmp')} {q(paths['done'])}; exit \"$code\"; }}\n"
            "trap finish EXIT\n"
            f"apptainer exec --nv {q(module.SIF)} bash -c {q(inner_command(paths, settings))} 2>&1 | tee {q(paths['log'])}\n"
            'exit "${PIPESTATUS[0]}"\n')


def submit(module, hpc, args):
    image = args.image.resolve()
    payload = image.read_bytes()
    from PIL import Image
    with Image.open(image) as source:
        source.verify()
    if image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("PNG/JPG/WEBPを指定してください。")
    requested_seed = getattr(args, "seed", None)
    settings = {"pipeline_type": "1536_cascade", "seed": requested_seed if requested_seed is not None else "random",
                "texture_size": args.texture_size, "decimation_target": args.decimation_target,
                "normal_map": args.normal_map, "save_highres": args.save_highres}
    keep_models_on_gpu = getattr(args, "keep_models_on_gpu", False)
    if keep_models_on_gpu:
        settings["low_vram"] = False
    attempt = getattr(args, "attempt", 1)
    attempt_tag = str(attempt).encode() if attempt > 1 else b""
    digest = hashlib.sha256(payload + json.dumps(settings, sort_keys=True).encode() + attempt_tag).hexdigest()
    name = "trellis_" + digest[:24]
    job = module.Job(name)
    if job.path.exists():
        if not job.step("generate").get("state"):
            raise RuntimeError("送信前に停止した記録があります。job.jsonを確認してください。再実行は開始していません。")
        print(f"既存の処理を再利用します: {name}", flush=True)
        return job
    job.data.update(request=settings, attempt=attempt, image_source=str(image), image_sha256=hashlib.sha256(payload).hexdigest())
    job.save()
    images_dir = job.dir / "images"
    images_dir.mkdir()
    local_image = images_dir / ("object" + image.suffix.lower())
    local_image.write_bytes(payload)
    paths = module.remote_paths(hpc.home, job)
    job.data["remote_dir"] = paths["job"]
    job.save()
    # Preserve the installed runner for provenance, without modifying the shared HPC scripts.
    code, out, err = hpc.run(remote_python('''from pathlib import Path
import json
root = Path.home() / "kuro" / "TRELLIS.2"
source = (root / "batch_trellis_glb.py").read_text()
assert 'pipeline_type="1536_cascade"' in source.replace(" ", ""), "Unexpected generation resolution"
assert "seed=42" in source.replace(" ", ""), "Unexpected seed"
assert (root / "normal_bake.py").is_file(), "normal_bake.py missing"
print("VERIFIED")
'''))
    if code != 0 or out.strip() != "VERIFIED":
        raise RuntimeError("HPC側の1536設定または依存ファイルを確認できませんでした。")
    hpc.get([f"{hpc.home}/kuro/TRELLIS.2/batch_trellis_glb.py"], job.dir)
    source = (job.dir / "batch_trellis_glb.py").read_text(encoding="utf-8")
    (job.dir / "batch_trellis_job.py").write_text(job_runner(source, requested_seed, keep_models_on_gpu), encoding="utf-8")
    print(f"画像をHPCへ送信: {name}", flush=True)
    module.upload(hpc, job, paths, [local_image])
    bootstrap = '''import faulthandler, runpy, signal, sys, os
faulthandler.enable()
faulthandler.register(signal.SIGUSR1, all_threads=True)
faulthandler.dump_traceback_later(120, repeat=True)
assert sys.argv[1] == "--runner-code"
runner = sys.argv[2]
sys.argv = [runner] + sys.argv[3:]
sys.path.insert(0, os.getcwd())
print("Diagnostic traceback enabled", flush=True)
runpy.run_path(runner, run_name="__main__")
'''
    (job.dir / "bootstrap.py").write_text(bootstrap, encoding="utf-8")
    (job.dir / "run_interactive.sh").write_text(interactive_script(module, paths, settings), encoding="utf-8", newline="\n")
    hpc.put(job.dir, ["bootstrap.py", "batch_trellis_job.py", "run_interactive.sh"], paths["job"])
    step = job.step("generate")
    if getattr(args, "command", "submit") == "stage":
        step.update(state="prepared", settings=settings)
        job.save()
        print("準備完了。生成は開始していません。SSH接続後に次を実行してください:", flush=True)
        print("bash " + shlex.quote(paths["job"] + "/run_interactive.sh"), flush=True)
        return job
    step.update(state="submitting", settings=settings)
    job.save()
    command = generation_command(module, paths, settings)
    code, out, err = hpc.run(command)
    if code != 0 or not out.strip().isdigit():
        step["state"] = "submission_unknown"
        job.save()
        raise RuntimeError("開始の応答を確認できません。自動再送せず、statusで確認してください。")
    step.update(state="running", pid=int(out.strip()), command=command)
    job.save()
    print(f"HPCで生成開始: {name} / PID {step['pid']}", flush=True)
    return job


def load_job(module, name):
    if not re.fullmatch(r"trellis_[0-9a-f]{24}", name):
        raise ValueError("Invalid job name")
    job = module.Job(name)
    if not job.path.is_file():
        raise FileNotFoundError("処理の記録がありません。")
    return job


def status(module, hpc, job):
    paths = module.remote_paths(hpc.home, job)
    source = ("from pathlib import Path; import json; "
              f"p=Path({paths['job']!r}); d=p/'DONE'; log=p/'log.txt'; "
              "print(json.dumps({'done':d.read_text().strip() if d.exists() else None, "
              "'tail':log.read_text(errors='replace')[-2000:] if log.exists() else ''}))")
    code, out, err = hpc.run(remote_python(source))
    if code:
        raise RuntimeError("HPCの実行状況を取得できません。")
    result = json.loads(out)
    if result["done"] is not None:
        job.step("generate").update(state="finished", exit_code=result["done"])
        job.save()
    return result


def resources(hpc, job):
    pid = int(job.step("generate").get("pid") or 0)
    source = '''from pathlib import Path
import json, subprocess
root_pid = ROOT_PID
image_marker = IMAGE_MARKER
memory = {}
for line in Path('/proc/meminfo').read_text().splitlines():
    key, value = line.split(':', 1)
    if key in ('MemTotal', 'MemAvailable'):
        memory[key] = int(value.split()[0]) * 1024
result = {'host_bytes': memory, 'processes': [], 'cgroup_limits': []}
try:
    gpu = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu,power.draw,temperature.gpu',
                          '--format=csv,noheader'], capture_output=True, text=True, timeout=15)
    result['gpu_utilization_power_temperature'] = gpu.stdout.strip()
except Exception as exc:
    result['gpu_check_error'] = type(exc).__name__
pending, seen, cgroups = [root_pid], set(), set()
if not root_pid:
    for candidate in Path('/proc').iterdir():
        if not candidate.name.isdigit(): continue
        try:
            args = (candidate/'cmdline').read_bytes().split(bytes([0]))
            if image_marker.encode() in args and (candidate/'exe').resolve().name.startswith('python'):
                pending.append(int(candidate.name))
        except (FileNotFoundError, PermissionError): pass
while pending:
    pid = pending.pop()
    if pid <= 0 or pid in seen:
        continue
    seen.add(pid)
    proc = Path('/proc') / str(pid)
    if not proc.exists():
        continue
    fields = {}
    for line in (proc / 'status').read_text().splitlines():
        key, value = line.split(':', 1)
        if key in ('Name', 'State', 'VmRSS', 'VmSize', 'VmPeak'):
            fields[key] = value.strip()
    fields['pid'] = pid
    result['processes'].append(fields)
    children = proc / 'task' / str(pid) / 'children'
    if children.exists():
        pending.extend(int(p) for p in children.read_text().split())
    for entry in (proc / 'cgroup').read_text().splitlines():
        hierarchy, controllers, relative = entry.split(':', 2)
        if hierarchy == '0':
            group = Path('/sys/fs/cgroup') / relative.lstrip('/')
            for parent in [group] + list(group.parents):
                if str(parent).startswith('/sys/fs/cgroup'):
                    cgroups.add(parent)
for group in sorted(cgroups):
    values = {'path': str(group)}
    for key in ('memory.max', 'memory.high', 'memory.current'):
        path = group / key
        if path.is_file():
            values[key] = path.read_text().strip()
    if len(values) > 1:
        result['cgroup_limits'].append(values)
print(json.dumps(result, indent=2))
'''.replace('ROOT_PID', str(pid)).replace('IMAGE_MARKER', repr(job.data['remote_dir'] + '/images'))
    code, out, err = hpc.run(remote_python(source))
    if code:
        raise RuntimeError("HPCのメモリ情報を取得できませんでした。")
    print(out, flush=True)


def diagnose(hpc):
    source = '''from pathlib import Path
import subprocess, shutil
root = Path.home() / 'kuro' / 'TRELLIS.2'
path = root / 'trellis2' / 'pipelines' / 'trellis2_image_to_3d.py'
text = path.read_text()
for method in ('to', 'decode_tex_slat'):
    start = text.index('    def ' + method + '(')
    end = text.index('    def ', start + 10)
    print(text[start:end])
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit(): continue
    try:
        cmd = (proc/'cmdline').read_bytes().replace(bytes([0]), b' ').decode(errors='replace')
        if 'python -u batch_trellis_glb.py' not in cmd and '/bootstrap.py --runner-code' not in cmd: continue
        print('PROCESS', proc.name)
        print('WAIT_CHANNEL', (proc/'wchan').read_text())
        tasks = list((proc/'task').iterdir())
        print('THREADS', len(tasks))
        counts = {}
        for task in tasks:
            state = (task/'wchan').read_text()
            counts[state] = counts.get(state, 0) + 1
        print('THREAD_WAITS', counts)
    except (OSError, PermissionError): pass
for name in ('py-spy', 'gdb', 'strace'):
    print('DEBUG_TOOL', name, shutil.which(name))
'''
    code, out, err = hpc.run(remote_python(source))
    print(out, flush=True)
    if code:
        raise RuntimeError("HPC診断に失敗しました。")


def cancel(module, hpc, job):
    paths = module.remote_paths(hpc.home, job)
    source = '''from pathlib import Path
import os, signal
marker = JOB_IMAGES
killed = []
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit(): continue
    try:
        args = (proc / 'cmdline').read_bytes().split(bytes([0]))
        if marker.encode() not in args: continue
        if not (proc / 'exe').resolve().name.startswith('python'): continue
        os.kill(int(proc.name), signal.SIGTERM)
        killed.append(proc.name)
    except (FileNotFoundError, PermissionError, ProcessLookupError): pass
print('Stopped only matching generation processes:', killed)
'''.replace('JOB_IMAGES', repr(paths['images']))
    code, out, err = hpc.run(remote_python(source))
    print(out, flush=True)
    if code:
        raise RuntimeError("今回の生成処理を停止できませんでした。")
    job.step("generate")["cancellation_requested"] = True
    job.save()


def validate_glb(path):
    with path.open("rb") as stream:
        magic, version, length = struct.unpack("<4sII", stream.read(12))
        if magic != b"glTF" or version != 2 or length != path.stat().st_size:
            raise ValueError("Invalid GLB header: " + path.name)
        chunk_size, chunk_type = struct.unpack("<II", stream.read(8))
        if chunk_type != 0x4E4F534A or chunk_size > length - 20:
            raise ValueError("Invalid GLB JSON chunk")
        document = json.loads(stream.read(chunk_size))
    primitives = [p for mesh in document.get("meshes", []) for p in mesh["primitives"]]
    if not primitives:
        raise ValueError("GLB has no geometry")
    accessors = document.get("accessors", [])
    faces = sum(accessors[p["indices"]]["count"] // 3 for p in primitives if "indices" in p)
    materials = document.get("materials", [])
    return {"file": str(path.resolve()), "bytes": length, "faces": faces,
            "materials": len(materials), "images": len(document.get("images", [])),
            "normal_maps": sum("normalTexture" in m for m in materials),
            "pbr_materials": sum("pbrMetallicRoughness" in m for m in materials)}


def fetch(module, hpc, job):
    result = status(module, hpc, job)
    if result["done"] is None:
        print("生成中です。", flush=True)
        return False
    paths = module.remote_paths(hpc.home, job)
    module.download(hpc, job, paths)
    manifest_path = job.dir / "GLBs" / "manifest.json"
    if result["done"] != "0" or not manifest_path.is_file():
        raise RuntimeError("HPCで生成に失敗しました。保存したlog.txtを確認してください。")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assets = manifest.get("assets", [])
    if not assets or any(a["status"] != "success" for a in assets):
        raise RuntimeError("HPCの画像別生成結果に失敗があります。manifest.jsonを確認してください。")
    summaries = [validate_glb(p) for p in sorted((job.dir / "GLBs").glob("*.glb"))]
    if not summaries or not any(s["pbr_materials"] and s["images"] for s in summaries):
        raise RuntimeError("材質付きGLBを確認できませんでした。")
    if job.data["request"]["normal_map"] and not any(s["normal_maps"] for s in summaries):
        raise RuntimeError("要求したノーマルマップを確認できませんでした。")
    job.step("verify").update(done=True, files=summaries)
    job.save()
    print(f"受け取り・検証完了: {job.dir / 'GLBs'}", flush=True)
    print(json.dumps(summaries, ensure_ascii=False, indent=2), flush=True)
    return True


@contextmanager
def receiver_lock(root):
    """Only one Windows receiver may download into this job directory at a time."""
    root.mkdir(parents=True, exist_ok=True)
    stream = (root / ".receiver.lock").open("a+b")
    locked = False
    try:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


def watch_results(module, hpc, poll_seconds=30, once=False):
    """Receive known manual SSH jobs; this function NEVER submits or starts generation."""
    last_errors = {}
    while True:
        for path in sorted(module.LOCAL_JOBS.glob("trellis_*/job.json")):
            name = path.parent.name
            if not re.fullmatch(r"trellis_[0-9a-f]{24}", name):
                continue
            try:
                job = load_job(module, name)
                if job.step("verify").get("done") or job.step("auto_receive").get("state") == "failed":
                    continue
                if not job.data.get("remote_dir"):
                    continue
                if job.step("generate").get("exit_code") not in (None, "0", 0):
                    continue
                result = status(module, hpc, job)
                if result["done"] is None:
                    continue
                if result["done"] != "0":
                    job.step("auto_receive").update(state="failed", reason="generation_failed", exit_code=result["done"])
                    job.save()
                    print(f"[{name}] HPCの生成が終了コード{result['done']}で停止。自動再生成はしません。", flush=True)
                    continue
                print(f"[{name}] 完了を検知。Windowsへ受信します。", flush=True)
                if fetch(module, hpc, job):
                    job.step("auto_receive").update(state="success", output=str((job.dir / "GLBs").resolve()))
                    job.save()
                    last_errors.pop(name, None)
            except (Exception, SystemExit) as exc:
                error = type(exc).__name__
                if last_errors.get(name) != error:
                    print(f"[{name}] 受信を保留: {error}。次の確認時に再接続します。", flush=True)
                    last_errors[name] = error
        if once:
            return
        time.sleep(poll_seconds)


def main(argv=None):
    parser = argparse.ArgumentParser(description="既存のSSH設定でHPCのTRELLIS.2を実行します。")
    parser.add_argument("command", choices=["connect", "watch-results", "inspect", "diagnose", "stage", "submit", "status", "resources", "cancel", "fetch", "run"])
    parser.add_argument("--image", type=Path)
    parser.add_argument("--job")
    parser.add_argument("--texture-size", type=int, choices=[2048, 4096, 8192], default=4096)
    parser.add_argument("--decimation-target", type=int, default=150000)
    parser.add_argument("--normal-map", action="store_true")
    parser.add_argument("--save-highres", action="store_true")
    parser.add_argument("--attempt", type=int, default=1, help="明示的に再生成する場合の試行番号")
    parser.add_argument("--seed", type=int, default=None, help="省略時はHPCで実行するたびランダムに選択して記録")
    parser.add_argument("--keep-models-on-gpu", action="store_true", help="CPU退避を無効にする。より多くのメモリを使用")
    parser.add_argument("--poll-seconds", type=float, default=30, help="自動受信の確認間隔（秒）")
    parser.add_argument("--once", action="store_true", help="自動受信の確認を1回だけ行って終了")
    args = parser.parse_args(argv)
    if not 1 <= args.poll_seconds <= 3600:
        parser.error("poll-seconds must be between 1 and 3600")
    module = transport()
    hpc = module.HPC(module.HPC_JSON, module.HOST)
    if args.command == "watch-results":
        try:
            with receiver_lock(module.LOCAL_JOBS):
                (module.LOCAL_JOBS / ".receiver.pid").write_text(str(os.getpid()), encoding="ascii")
                print(f"HPC結果の自動受信を開始: {module.LOCAL_JOBS}", flush=True)
                try:
                    watch_results(module, hpc, args.poll_seconds, args.once)
                finally:
                    (module.LOCAL_JOBS / ".receiver.pid").unlink(missing_ok=True)
        except OSError:
            print("自動受信はすでに起動中か、受信フォルダを開けません。", flush=True)
            return 1
        return 0
    if args.command == "connect":
        print("HPCへSSH接続します。接続後のプロンプトでコマンドを入力してください。", flush=True)
        print("確認コマンド: hostname / whoami / pwd。終了するには exit。", flush=True)
        return subprocess.call([hpc.ssh, *hpc.opts, hpc.host], env=hpc.env)
    if args.command == "inspect":
        inspect(hpc)
        return
    if args.command == "diagnose":
        diagnose(hpc)
        return
    if args.decimation_target <= 0 or args.attempt < 1:
        parser.error("decimation-target must be positive")
    if args.command in {"stage", "submit", "run"}:
        if not args.image:
            parser.error("--image is required")
        job = submit(module, hpc, args)
    else:
        if not args.job:
            parser.error("--job is required")
        job = load_job(module, args.job)
    if args.command == "status":
        print(json.dumps(status(module, hpc, job), ensure_ascii=False, indent=2))
    elif args.command == "resources":
        resources(hpc, job)
    elif args.command == "cancel":
        cancel(module, hpc, job)
    elif args.command == "fetch":
        fetch(module, hpc, job)
    elif args.command == "run":
        while True:
            state = status(module, hpc, job)
            if state["done"] is not None:
                fetch(module, hpc, job)
                break
            tail = state["tail"].splitlines()
            print(tail[-1][-200:] if tail else "HPCで実行中...", flush=True)
            time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
