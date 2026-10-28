#!/usr/bin/env python3
"""Host-side, bounded recovery for running containers marked unhealthy.

Dry-run by default. Never starts stopped containers. No Docker socket in the model.
"""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import time


def decide(state, inspection, now, policy):
    state = dict(state)
    state['attempts'] = [t for t in state.get('attempts', []) if now - t < policy.window]
    if inspection.get('Status') != 'running' or inspection.get('Restarting'):
        state['failures'] = 0
        return 'skip-not-running', state
    if inspection.get('Health', {}).get('Status') != 'unhealthy':
        state['failures'] = 0
        return 'skip-not-unhealthy', state
    state['failures'] = state.get('failures', 0) + 1
    if state['failures'] < policy.failures:
        return 'observe', state
    if len(state['attempts']) >= policy.max_restarts:
        return 'alert-budget-exhausted', state
    if state['attempts'] and now - state['attempts'][-1] < policy.cooldown:
        return 'cooldown', state
    return 'restart', state


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data) + '\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='/opt/qwen38')
    parser.add_argument('--state-dir', default='/var/lib/qwen38-watchdog')
    parser.add_argument('--failures', type=int, default=3)
    parser.add_argument('--cooldown', type=int, default=1800)
    parser.add_argument('--window', type=int, default=3600)
    parser.add_argument('--max-restarts', type=int, default=2)
    parser.add_argument('--restart', action='store_true', help='Enable actual recovery; otherwise dry-run')
    args = parser.parse_args()
    if min(args.failures, args.cooldown, args.window, args.max_restarts) < 1:
        parser.error('Recovery thresholds must be positive')
    directory = Path(args.state_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory/'lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('WATCHDOG skip: another check is running')
            return 0
        if (directory/'disabled').exists():
            print('WATCHDOG disabled for maintenance')
            return 0
        project = Path(args.project_dir).resolve()
        command = ['docker', 'compose', '--project-directory', str(project),
                   '-f', str(project/'compose.yaml')]
        ids = subprocess.check_output(command + ['ps', '--all', '-q', 'qwen'], text=True).split()
        if not ids:
            print('WATCHDOG skip: service is absent')
            return 0
        if len(ids) != 1:
            raise RuntimeError('Expected one qwen container; refusing ambiguous recovery')
        identifier = ids[0]
        inspect_command = ['docker', 'inspect', '--format', '{{json .State}}', identifier]
        inspection = json.loads(subprocess.check_output(inspect_command, text=True))
        path = directory/'state.json'
        state = json.loads(path.read_text()) if path.exists() else {}
        if state.get('container') != identifier:
            state['failures'] = 0
        state['container'] = identifier
        now = time.time()
        action, state = decide(state, inspection, now, args)
        print(f'WATCHDOG action={action} failures={state["failures"]} attempts={len(state["attempts"])}')
        if action == 'restart' and args.restart:
            # Recheck immediately before mutation; maintenance/stopped states win.
            current = json.loads(subprocess.check_output(inspect_command, text=True))
            if ((directory/'disabled').exists() or current.get('Status') != 'running'
                    or current.get('Health', {}).get('Status') != 'unhealthy'):
                print('WATCHDOG skip: state changed before restart')
                return 0
            # Persist the attempt before calling Docker, including failed/uncertain calls.
            state['attempts'].append(now)
            state['failures'] = 0
            save(path, state)
            subprocess.run(['docker', 'restart', '--time', '120', identifier], check=True, timeout=150)
        else:
            if action == 'restart':
                print('WATCHDOG dry-run: restart was not executed')
            save(path, state)
        return 2 if action == 'alert-budget-exhausted' else 0


if __name__ == '__main__':
    raise SystemExit(main())
