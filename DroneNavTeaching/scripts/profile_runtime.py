"""True model/render/SLAM profiling in an isolated Platform instance.

Reuses only this project's local navigation/recognition services. Does not
modify the browser's Platform state; all GL work stays on its owner thread.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
import threading
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np
from app import server
from app.simulator import DroneSimulator, EpisodeCatalog
from localization import FusedLocalizer
from navigation import NavigationPolicy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='current')
    parser.add_argument('--steps', type=int, default=12)
    parser.add_argument('--scene-index', type=int, default=0)
    parser.add_argument('--episode-index', type=int, default=1)
    args = parser.parse_args()
    if args.label == 'before':
        parser.error('before is the frozen historical V1 baseline; use --label current to profile this V2 source')
    from scripts.manage import SERVICES
    try:
        with urlopen('http://127.0.0.1:%d/api/state' % SERVICES['platform']['port'], timeout=3) as response:
            live = json.load(response)
    except OSError:
        parser.error('Start the managed services first; this profiler does not start models')
    if live.get('has_started') or live.get('status') != 'idle':
        parser.error('Profiling shares the navigation service: reset the classroom UI to idle and do not use it during profiling')
    timings = defaultdict(list)
    marks = {'last_observation': None, 'policy_frame': None, 'first_policy': None,
             'observations': 0, 'rendered_frames': 0}
    original_choose = EpisodeCatalog.choose
    EpisodeCatalog.choose = lambda self, **kw: original_choose(self, scene_index=args.scene_index, episode_index=args.episode_index)
    original_observe = DroneSimulator.observe
    def observe(self, *a, **kw):
        begin = time.perf_counter()
        result = original_observe(self, *a, **kw)
        timings['render_ms'].append((time.perf_counter()-begin)*1000)
        marks['last_observation'] = begin
        marks['observations'] += 1
        return result
    DroneSimulator.observe = observe
    original_track = FusedLocalizer.track
    def track(self, *a, **kw):
        begin = time.perf_counter()
        result = original_track(self, *a, **kw)
        timings['fused_track_ms'].append((time.perf_counter()-begin)*1000)
        timings['orb_native_ms'].append(result.get('orb_latency_ms', 0))
        return result
    FusedLocalizer.track = track
    original_act = NavigationPolicy.act
    def act(self, *a, **kw):
        begin = time.perf_counter()
        marks['policy_frame'] = marks['last_observation'] or begin
        marks['first_policy'] = marks['first_policy'] or begin
        action = original_act(self, *a, **kw)
        timings['policy_roundtrip_ms'].append((time.perf_counter()-begin)*1000)
        timings['policy_model_ms'].append(self.last_info.get('inference_ms', 0))
        return action
    NavigationPolicy.act = act
    original_advance = DroneSimulator.advance
    def advance(self, *a, **kw):
        frames = original_advance(self, *a, **kw)
        first = True
        for frame in frames:
            if args.label == 'before' and first and marks['policy_frame'] is not None:
                timings['visual_to_first_motion_ms'].append((time.perf_counter()-marks['policy_frame'])*1000)
                first = False
            marks['rendered_frames'] += 1
            yield frame
    DroneSimulator.advance = advance
    if args.label != 'before' and hasattr(server.Platform, '_record_dispatch'):
        original_dispatch = server.Platform._record_dispatch
        def dispatch(self, decision):
            original_dispatch(self, decision)
            timings['visual_to_first_motion_ms'].append(self.e2e_latencies[-1])
        server.Platform._record_dispatch = dispatch
    original_encode = cv2.imencode
    def encode(*a, **kw):
        begin = time.perf_counter()
        result = original_encode(*a, **kw)
        timings['jpeg_encode_ms'].append((time.perf_counter()-begin)*1000)
        return result
    cv2.imencode = encode
    platform = server.Platform()
    begin = time.perf_counter()
    samples = []
    platform.command('start')
    deadline = begin + 180
    try:
        while time.perf_counter() < deadline:
            t = time.perf_counter()
            snapshot = platform.snapshot()
            timings['snapshot_clean_ms'].append((time.perf_counter()-t)*1000)
            t = time.perf_counter()
            body = json.dumps(snapshot, ensure_ascii=False).encode()
            timings['json_encode_ms'].append((time.perf_counter()-t)*1000)
            timings['state_payload_bytes'].append(len(body))
            samples.append({'wall': time.perf_counter(), 'sim': snapshot.get('frame_timestamp', 0),
                            'steps': snapshot['steps'], 'frame_seq': snapshot['frame_seq']})
            if snapshot['status'] == 'error':
                raise RuntimeError(snapshot.get('error'))
            if snapshot['steps'] >= args.steps or (snapshot.get('stop_reason') == 'MODEL_STOP'):
                break
            time.sleep(.08)
        platform.command('pause')
        time.sleep(.4)
        snapshot = platform.snapshot()
        runtime = time.perf_counter() - (marks['first_policy'] or begin)
        def summary(values):
            return {'count': len(values), 'mean': float(np.mean(values)), 'p50': float(np.percentile(values,50)),
                    'p95': float(np.percentile(values,95)), 'max': float(np.max(values))} if values else {}
        measured = {name: summary(values) for name, values in timings.items()}
        frames = snapshot['frame_seq']-1 if args.label != 'before' else marks['rendered_frames']
        result = {'label': args.label, 'scene_index': args.scene_index, 'episode_index': args.episode_index,
                  'scene': snapshot.get('scene'), 'target': snapshot.get('target'),
                  'steps': snapshot['steps'], 'frames': frames, 'observation_calls': marks['observations'],
                  'simulation_seconds': snapshot.get('frame_timestamp',0), 'run_wall_seconds': runtime,
                  'real_time_factor': snapshot.get('frame_timestamp',0) / runtime,
                  'frame_delivery_hz': frames/runtime,
                  'end_to_end_hz': 1000/measured['visual_to_first_motion_ms']['mean'] if timings['visual_to_first_motion_ms'] else None,
                  'e2e_scope': 'camera observation begin through actual first rendered motion substep of its semantic navigation decision',
                  'slam_mean_error_m': snapshot.get('slam_mean_error_m'),
                  'missing_tracking_frames': snapshot.get('missing_tracking_frames'),
                  'tracking': snapshot.get('tracking'), 'components': measured,
                  'backend_performance': snapshot.get('performance'), 'e2e': snapshot.get('e2e'),
                  'visual_pipeline_error_m': snapshot.get('visual_pipeline_error_m'),
                  'imu_samples': snapshot.get('imu_samples'), 'visual_imu_samples': snapshot.get('visual_imu_samples'),
                  'samples': samples, 'stop_reason': snapshot.get('stop_reason')}
        path = ROOT / 'reports/performance_before_after.json'
        data = json.loads(path.read_text()) if path.exists() else {}
        data[args.label] = result
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='samples'}, ensure_ascii=False, indent=2), flush=True)
    finally:
        platform.command('exit')
        until = time.perf_counter()+15
        while platform.state['has_started'] and time.perf_counter()<until:
            time.sleep(.05)
        platform.exit_requested = True
        platform.controller.shutdown.set()


if __name__ == '__main__':
    main()
