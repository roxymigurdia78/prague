"""Run one OpenSSH step with closed stdin and a bounded process lifetime."""
import argparse
import os
import subprocess
import sys
import time


def run_step(command, timeout, label):
    started = time.monotonic()
    print(f"HPC stage START: {label} (timeout {timeout}s)", flush=True)
    # OpenSSH must not consume the build terminal's input. In particular, a
    # remote command that has finished must not keep waiting on inherited stdin.
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL)
    try:
        code = process.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        if os.name == "nt":
            # Include the cloudflared ProxyCommand child, only for this step.
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=5, check=False)
            except subprocess.TimeoutExpired:
                pass
            if process.poll() is None:
                process.kill()
        else:
            process.kill()
        process.wait(timeout=15)
        print(f"HPC stage STOP: {label}: {type(exc).__name__}", file=sys.stderr, flush=True)
        return 124 if isinstance(exc, subprocess.TimeoutExpired) else 130
    print(f"HPC stage END: {label} ({time.monotonic()-started:.1f}s, exit {code})", flush=True)
    return code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.timeout <= 0:
        parser.error("A command and positive timeout are required")
    return run_step(command, args.timeout, args.label)


if __name__ == "__main__":
    sys.exit(main())
