"""Real-model smoke check for noisy IMU and measured controller telemetry."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def api(path, method='GET'):
    request = urllib.request.Request('http://127.0.0.1:5800/api/' + path, method=method)
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def main():
    started = time.perf_counter()
    report, process = {}, None
    env = dict(os.environ, PYTHONNOUSERSITE='1')
    (ROOT / 'logs').mkdir(exist_ok=True)
    try:
        with (ROOT / 'logs/adjustments-runtime.log').open('w') as log:
            process = subprocess.Popen(['bash', str(ROOT / 'start.sh'), '--no-browser', '--timeout', '240'],
                                       cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Startup failed; inspect logs/adjustments-runtime.log')
                try:
                    if api('health').get('ready'):
                        break
                except Exception:
                    pass
                time.sleep(.2)
            else:
                raise TimeoutError('Startup timed out')
            api('start', 'POST')
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                state = api('state')
                if state.get('error'):
                    raise RuntimeError(state['error'])
                if state.get('imu_samples', 0) >= 60 and (state.get('steps', 0) >= 3 or state.get('status') == 'paused'):
                    break
                time.sleep(.2)
            else:
                raise TimeoutError('No sufficient sensor samples / actions')
            assert state['imu_noise']['accel_std_m_s2'] > 0
            assert state['imu_noise']['gyro_std_rad_s'] > 0
            assert 0 < state['slam_mean_error_m'] <= .1
            assert state['controller']['measured_hz'] > 20
            report['runtime'] = {key: state.get(key) for key in
                                 ('scene', 'steps', 'imu_samples', 'imu_noise', 'localization_source',
                                  'slam_mean_error_m', 'slam_error_m', 'controller', 'e2e')}
            if state['status'] == 'running':
                api('pause', 'POST')
            api('exit', 'POST')
            process.wait(timeout=40)
            assert process.returncode == 0
        ports = {}
        for port in (5800, 5811, 8766):
            with socket.socket() as connection:
                connection.settimeout(.3)
                ports[str(port)] = connection.connect_ex(('127.0.0.1', port)) == 0
        assert not any(ports.values())
        report.update(passed=True, open_ports_after=ports,
                      gpu_compute_after=subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid,process_name',
                                                               '--format=csv,noheader'], text=True).splitlines())
        assert not report['gpu_compute_after']
    except Exception as error:
        report.update(passed=False, error=repr(error))
    finally:
        if process and process.poll() is None:
            subprocess.run(['bash', str(ROOT / 'stop.sh')], cwd=str(ROOT), env=env, timeout=40, check=False)
            process.wait(timeout=15)
        report['duration_s'] = time.perf_counter() - started
        (ROOT / 'reports/adjustments_runtime.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
