"""Read live health and verify ready services are reused without spawning."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    spec = importlib.util.spec_from_file_location('teaching_manage', ROOT / 'scripts/manage.py')
    manage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manage)
    statuses = {name: manage.status(name) for name in manage.SERVICES}
    active = manage._active_state()
    if not all(manage._ready(value) for value in statuses.values()):
        print('Platform services are stopped or starting. Run bash start.sh before this read-only live check.')
        return 2
    spawned = []
    original = manage.subprocess.Popen
    def forbid_spawn(*args, **kwargs):
        spawned.append(args)
        raise AssertionError('Unexpected duplicate service spawn')
    manage.subprocess.Popen = forbid_spawn
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            for name in manage.SERVICES:
                assert statuses[name] and statuses[name].get('ready', True), (name, statuses[name])
                manage.start(name)
        original_status = manage.status
        try:
            manage.status = lambda name: {'ready': False}
            not_ready_output = io.StringIO()
            with contextlib.redirect_stdout(not_ready_output):
                manage.start('navigation')
            not_ready_correct = 'starting/not ready' in not_ready_output.getvalue() and not spawned
        finally:
            manage.status = original_status
    finally:
        manage.subprocess.Popen = original
    interpreters = {name: {'path': info['python'], 'exists': Path(info['python']).is_file(),
                           'executable': os.access(info['python'], os.X_OK), 'args': info['args'],
                           'managed_log': str(ROOT / 'logs' / (name + '.log'))}
                    for name, info in manage.SERVICES.items()}
    def state():
        with urlopen('http://127.0.0.1:5800/api/state', timeout=3) as response:
            return json.load(response)
    first = state()
    time.sleep(.25)
    second = state()
    paused = first['status'] == second['status'] == 'paused'
    pause_snapshot = {'status_before': first['status'], 'status_after': second['status'],
                      'steps_before': first['steps'], 'steps_after': second['steps'],
                      'position_before': first['position'], 'position_after': second['position'],
                      'frame_seq_before': first['frame_seq'], 'frame_seq_after': second['frame_seq'],
                      'controller_active': (second.get('controller') or {}).get('active'),
                      'stable_while_paused': paused and first['steps'] == second['steps'] and
                                              first['position'] == second['position'] and first['frame_seq'] == second['frame_seq']}
    report = {'ready_service_health': statuses, 'spawn_attempts': len(spawned),
              'lifecycle_version': 2, 'supervisor_active': bool(active),
              'supervisor_pid': active['supervisor']['pid'] if active else None,
              'managed_roles': sorted(active.get('services', {})) if active else [],
              'reuse_output': output.getvalue().splitlines(), 'interpreters': interpreters,
              'not_ready_existing_service_preserved': not_ready_correct,
              'not_ready_output': not_ready_output.getvalue().strip(),
              'read_only_live_pause_snapshot': pause_snapshot,
              'passed': not spawned and not_ready_correct and
                        all(x['exists'] and x['executable'] for x in interpreters.values()) and
                        (pause_snapshot['stable_while_paused'] if paused else True)}
    path = ROOT / 'reports/management_verification.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
