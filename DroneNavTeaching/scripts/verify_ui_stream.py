"""Read-only verification of the browser's actual MJPEG and compact state APIs."""
import json
from pathlib import Path
import re
import socket
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:5800'


def snapshot():
    with urlopen(BASE + '/api/state', timeout=5) as response:
        raw = response.read()
    return json.loads(raw), len(raw)


def main():
    before, size = snapshot()
    if before['status'] != 'running':
        raise RuntimeError('Start a simulation before this read-only stream check')
    received, tail = {}, b''
    started = time.monotonic()
    with urlopen(BASE + '/api/stream.mjpg?view=front', timeout=6) as response:
        content_type = response.headers.get('Content-Type', '')
        while time.monotonic() - started < 4:
            tail += response.read(16384)
            for value in re.findall(rb'X-Frame-Seq: (\d+)\r\n', tail):
                received.setdefault(int(value), time.monotonic())
            tail = tail[-100:]
    after, _ = snapshot()
    times = list(received.values())
    fps = (len(times)-1)/(times[-1]-times[0]) if len(times) > 1 else 0.
    result = {'run_id': before.get('run_id'), 'actual_http_front_frames': len(times),
              'actual_http_front_fps': fps, 'state_payload_bytes': size,
              'state_has_map_arrays': any(k in before for k in ('map_points','trajectory')),
              'multipart_content_type': content_type,
              'mean_localization_error': after.get('slam_mean_error_m'),
              'end_to_end': after.get('e2e'),
              'passed': fps >= 15 and size < 12000 and 'multipart/' in content_type and
                        not any(k in before for k in ('map_points','trajectory'))}
    (ROOT/'reports/frontend_stream_verification.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
