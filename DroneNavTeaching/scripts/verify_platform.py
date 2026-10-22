"""V2 live integration: starts/resets the current session and exits all services.

Run only when intentionally ending the managed classroom session.
"""
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.manage import SERVICES
BASE = 'http://127.0.0.1:%d' % SERVICES['platform']['port']


def state():
    with urlopen(BASE + '/api/state', timeout=10) as response:
        return json.load(response)


def command(name):
    with urlopen(Request(BASE + '/api/' + name, method='POST'), timeout=10) as response:
        assert response.status == 202
        return json.load(response)


def map_state():
    with urlopen(BASE + '/api/map', timeout=10) as response:
        return json.load(response)


def wait_closed(timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(BASE + '/api/health', timeout=.5):
                pass
        except (URLError, OSError):
            return True
        time.sleep(.1)
    raise TimeoutError('API exit did not stop the platform HTTP service')


def wait(predicate, timeout=90):
    deadline = time.monotonic() + timeout
    s = None
    while time.monotonic() < deadline:
        try:
            s = state()
        except URLError:
            time.sleep(.15)
            continue
        if s['status'] == 'error':
            raise RuntimeError(s.get('error'))
        if predicate(s):
            return s
        time.sleep(.15)
    raise TimeoutError('Platform did not reach expected state: %r' % s)


def main():
    report = {'real_services': True, 'checks': {}}
    wait(lambda s: True, timeout=30)
    command('reset')
    initial = wait(lambda s: s['status'] == 'idle' and not s['has_started'])
    initial_map = map_state()
    with urlopen(BASE + '/api/frame.jpg') as response:
        assert response.status == 204
    report['checks']['initial_windows_empty'] = initial['position'] is None and not initial_map['map_points']
    report['checks']['state_is_compact'] = 'map_points' not in initial and 'trajectory' not in initial
    command('start')
    running = wait(lambda s: s['has_started'] and (s['steps'] >= 3 or s['status'] == 'paused'))
    report['first_run'] = {k: running.get(k) for k in ('run_id', 'scene', 'target', 'asset_loaded', 'steps', 'localization_source')}
    assert running['asset_loaded'] and running['scene_count'] == 36
    command('pause')
    time.sleep(.4)  # finish any frame already in progress
    before = state()
    time.sleep(.6)
    after = state()
    assert before['steps'] == after['steps'] and before['position'] == after['position']
    assert not after['controller']['active']
    report['checks']['pause_freezes_motion_and_control'] = True
    report['pause_state'] = {k: after.get(k) for k in ('steps','position','slam_mean_error_m','recognition_hz','controller')}
    # An early semantic STOP ends a run; otherwise verify ordinary resume.
    if after.get('stop_reason') != 'MODEL_STOP':
        command('start')
        resumed = wait(lambda s: s['steps'] > after['steps'] or s.get('stop_reason') == 'MODEL_STOP')
        report['checks']['resume_or_model_stop'] = True
        report['resume_steps'] = resumed['steps']
    command('reset')
    reset = wait(lambda s: s['status'] == 'idle' and not s['has_started'])
    reset_map = map_state()
    assert reset['position'] is None and not reset_map['map_points'] and not reset_map['trajectory'] and not reset.get('stop_reason')
    report['checks']['reset_clears_windows_and_stop_reason'] = True
    command('start')
    new = wait(lambda s: s['has_started'] and s['status'] in ('running','paused'))
    assert new['run_id'] != running['run_id']
    report['checks']['can_start_again_after_reset'] = True
    report['second_run'] = {k: new.get(k) for k in ('run_id','scene','target')}
    reply = command('exit')
    assert reply.get('status') == 'exiting'
    report['exit_reply'] = reply
    report['checks']['exit_stops_http_service'] = wait_closed()
    report['passed'] = all(report['checks'].values())
    (ROOT / 'reports/ui_acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
