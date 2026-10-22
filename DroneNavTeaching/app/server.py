"""Threaded local UI; all OpenGL simulation work stays on one owning thread."""
import argparse
import io
import json
import mimetypes
import os
import queue
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np

from .control import CommandPublisher
from localization.ideal_imu import IdealIMUIntegrator

ROOT = Path(__file__).resolve().parents[1]


def clean(value):
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


class PlatformBase:
    def __init__(self):
        self.lock = threading.RLock()
        self.commands = queue.Queue()
        self.intent_generation = 0
        self.exit_requested = False
        self.run_gate = threading.Event()
        self.controller = CommandPublisher(50)
        self.frame = self.overview = None
        self.rgb = None
        self.frame_seq = 0
        self.frame_time = 0.
        self.state = {'status': 'idle', 'has_started': False, 'message': '等待开始仿真', 'steps': 0,
                      'max_steps': 500, 'scene_count': 36, 'detections': [], 'position': None,
                      'trajectory': [], 'map_points': [], 'events': [], 'error': None}
        self.run_dir = None
        self.slam_errors = []
        self.missing_tracking = 0
        threading.Thread(target=self._engine, daemon=True, name='simulator-owner').start()
        threading.Thread(target=self._recognition, daemon=True, name='recognition-worker').start()

    def update(self, **kwargs):
        with self.lock:
            self.state.update(clean(kwargs))

    def event(self, text):
        with self.lock:
            self.state['events'] = (self.state['events'] + [{'time': time.strftime('%H:%M:%S'), 'text': text}])[-20:]

    def log(self, name, record, run_id=None):
        with self.lock:
            if run_id is not None and (not self.state['has_started'] or self.state.get('run_id') != run_id):
                return
            directory = self.run_dir
        if directory:
            with (directory / (name + '.jsonl')).open('a') as stream:
                stream.write(json.dumps(clean(record), ensure_ascii=False) + '\n')

    def command(self, name):
        if name not in ('start', 'pause', 'reset', 'exit'):
            raise ValueError('Unknown command')
        with self.lock:
            self.intent_generation += 1
            generation = self.intent_generation
        if name in ('pause', 'reset', 'exit'):
            self.run_gate.clear()
            self.controller.pause()
            self.update(status='paused' if name == 'pause' else ('resetting' if name == 'reset' else 'exited'),
                        message={'pause': '已暂停', 'reset': '正在释放场景', 'exit': '已退出仿真'}[name])
        if name != 'pause':
            self.commands.put((name, generation))
        if name in ('reset', 'exit') and hasattr(self, 'stream_condition'):
            with self.stream_condition:
                self.stream_condition.notify_all()




    def _engine(self):
        while not self.exit_requested:
            self._engine_loop()
            if not self.exit_requested:
                time.sleep(.1)


    def _write_summary(self):
        if self.run_dir:
            result = self.snapshot()
            result.pop('map_points', None)
            result.pop('trajectory', None)
            (self.run_dir / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))


class EpisodeWorkers:
    """Latest-image workers; every IMU sample is retained in the visual batch."""
    def __init__(self, platform, localizer, policy, generation):
        self.platform, self.localizer, self.policy = platform, localizer, policy
        self.generation = generation
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.latest = None
        self.imu_pending = []
        self.visual_seq = self.navigation_seq = -1
        self.result = None
        self.threads = [threading.Thread(target=self._navigation, daemon=True, name='navigation-prefetch'),
                        threading.Thread(target=self._visual, daemon=True, name='orb-mapping-worker')]
        for worker in self.threads:
            worker.start()

    def submit(self, packet):
        with self.condition:
            self.latest = packet
            if packet['imu'] is not None:
                self.imu_pending.append(packet['imu'])
            self.condition.notify_all()

    def take(self, allow_motion=True):
        with self.condition:
            if self.result is not None and self.result['action'] != 'STOP' and not allow_motion:
                return None
            result, self.result = self.result, None
            self.condition.notify_all()
            return result

    def _navigation(self):
        while not self.stop.is_set():
            with self.condition:
                self.condition.wait_for(lambda: self.stop.is_set() or (
                    self.platform.run_gate.is_set() and self.latest is not None and self.result is None and
                    self.platform.state['steps'] < 500 and self.latest['seq'] != self.navigation_seq), timeout=.1)
                if self.stop.is_set():
                    return
                if (not self.platform.run_gate.is_set() or self.latest is None or self.result is not None or
                        self.platform.state['steps'] >= 500 or self.latest['seq'] == self.navigation_seq):
                    continue
                packet = self.latest
                self.navigation_seq = packet['seq']
            try:
                begin = time.perf_counter()
                action = self.policy.act(packet['rgb'], packet['depth'], pose=packet['pose'])
                result = {'action': action, 'info': dict(self.policy.last_info), 'packet': packet,
                          'request_ms': (time.perf_counter()-begin)*1000}
                with self.condition:
                    if not self.stop.is_set():
                        self.result = result
                        self.condition.notify_all()
            except Exception as exc:
                with self.platform.lock:
                    if (self.stop.is_set() or self.platform.state.get('run_id') != packet['run_id'] or
                            self.platform.intent_generation != packet['generation'] or
                            not self.platform.run_gate.is_set()):
                        continue
                    self.platform.update(navigation_error=str(exc))
                    self.platform.run_gate.clear()
                    self.platform.controller.pause()
                    self.platform.update(status='error', error=str(exc), message='导航推理中断')

    def _visual(self):
        deadline = 0.
        try:
            while not self.stop.is_set():
                with self.condition:
                    self.condition.wait_for(lambda: self.stop.is_set() or (
                        self.latest is not None and self.latest['seq'] != self.visual_seq), timeout=.1)
                    if self.stop.is_set():
                        break
                    if self.latest is None or self.latest['seq'] == self.visual_seq:
                        continue
                if self.stop.wait(max(0., deadline-time.perf_counter())):
                    break
                with self.condition:
                    packet = self.latest
                    imu, self.imu_pending = self.imu_pending, []
                    self.visual_seq = packet['seq']
                began = time.perf_counter()
                result = self.localizer.track(packet['rgb'], packet['depth'], packet['timestamp'], imu=imu or None)
                self.platform._visual_result(packet, result, (time.perf_counter()-began)*1000, len(imu))
                deadline = began + .1  # up to 10 Hz mapping, all 30 Hz IMU samples included
        except Exception as exc:
            if not self.stop.is_set():
                self.platform.update(mapping_error=str(exc), visual_tracking='ERROR', tracking='IMU_ONLY')
        finally:
            self.localizer.close()

    def close(self):
        self.stop.set()
        with self.condition:
            self.condition.notify_all()
        for worker in self.threads:
            worker.join(timeout=3)


class Platform(PlatformBase):
    """30 Hz sensor/motion owner with asynchronous semantic and visual work."""
    def __init__(self):
        cv2.setNumThreads(1)
        self.frame_period = 1. / float(os.getenv('DRONENAV_FRAME_HZ', '30'))
        self.e2e_latencies = deque(maxlen=256)
        self.frame_intervals = deque(maxlen=300)
        self.stage_ms = {name: deque(maxlen=300) for name in ('render', 'frame', 'jpeg', 'mapping', 'policy')}
        self._inertial = None
        self._workers = None
        self._jpeg_packet = None
        self._jpeg_event = threading.Event()
        self._map_seq = 0
        self._last_frame_wall = None
        self._run_wall = None
        self._run_sim = 0.
        self._shutdown_scheduled = False
        super().__init__()
        self.stream_condition = threading.Condition(self.lock)
        threading.Thread(target=self._jpeg_worker, daemon=True, name='jpeg-latest-frame').start()

    def snapshot(self, include_map=False):
        with self.lock:
            result = {key: value for key, value in self.state.items()
                      if include_map or key not in ('map_points', 'trajectory')}
            result['frame_seq'] = self.frame_seq
            result['map_seq'] = self._map_seq
            mean = sum(self.e2e_latencies)/len(self.e2e_latencies) if self.e2e_latencies else None
            result['e2e'] = {'hz': 1000/mean if mean else None, 'mean_latency_ms': mean,
                             'samples': len(self.e2e_latencies),
                             'scope': 'camera frame -> navigation decision -> first executed motion command'}
            result['map_point_count'] = len(self.state.get('map_points', []))
            result['performance'] = {name+'_mean_ms': sum(data)/len(data) if data else None
                                     for name, data in self.stage_ms.items()}
            result['performance']['sensor_hz'] = (len(self.frame_intervals)/sum(self.frame_intervals)
                                                  if self.frame_intervals else None)
        result['controller'] = self.controller.metrics() if result['has_started'] else None
        return result

    def map_snapshot(self):
        with self.lock:
            return {'map_points': self.state.get('map_points', []), 'trajectory': self.state.get('trajectory', [])[-2000:],
                    'position': self.state.get('position'), 'map_seq': self._map_seq,
                    'run_id': self.state.get('run_id')}

    def _jpeg_worker(self):
        while not self.exit_requested:
            if not self._jpeg_event.wait(.2):
                continue
            with self.lock:
                packet = self._jpeg_packet
                self._jpeg_event.clear()
            if packet is None:
                continue
            begin = time.perf_counter()
            rgb, overview, seq, run_id = packet
            frame = cv2.imencode('.jpg', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 78])[1].tobytes()
            small = (cv2.imencode('.jpg', cv2.cvtColor(overview, cv2.COLOR_RGB2BGR),
                                 [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tobytes() if overview is not None else None)
            with self.lock:
                if self.state['has_started'] and self.state.get('run_id') == run_id:
                    self.frame, self.overview = frame, small
                    self.state['jpeg_seq'] = seq
                    self.stage_ms['jpeg'].append((time.perf_counter()-begin)*1000)
                    self.stream_condition.notify_all()

    def _visual_result(self, packet, result, latency_ms, sample_count):
        estimate = result.get('position')
        error = float(np.linalg.norm(np.asarray(estimate)-packet['gt'])) if estimate is not None else None
        cloud = np.asarray(result.get('map_points', []), dtype=float).reshape(-1, 3)
        finite_cloud = cloud[np.isfinite(cloud).all(axis=1)].tolist()
        with self.lock:
            if not self.state['has_started'] or self.state.get('run_id') != packet['run_id']:
                return
            self.stage_ms['mapping'].append(latency_ms)
            self.state.update(map_points=finite_cloud,
                              visual_tracking=result.get('visual_tracking'), slam_ms=latency_ms,
                              visual_pipeline_error_m=error, visual_imu_samples=result.get('imu_samples'),
                              visual_batch_imu_count=sample_count,
                              visual_mapping_timestamp=packet['timestamp'])
            self._map_seq += 1
        self.log('mapping', {'timestamp': packet['timestamp'], 'imu_batch': sample_count,
                             'position': estimate, 'error_m': error, 'latency_ms': latency_ms,
                             'visual_tracking': result.get('visual_tracking')}, run_id=packet['run_id'])

    def _new_frame(self, sim, rgb, depth, overview, localizer=None):
        began = time.perf_counter()
        imu = sim.last_imu
        if imu is not None:
            self._inertial.update(imu['gyro_body_rad_s'], imu['specific_force_body_m_s2'], imu['dt'])
        estimate = self._inertial.state()
        position = estimate['position']
        gt, _ = sim.camera_pose()  # evaluator only, never estimator or policy input
        error = float(np.linalg.norm(np.asarray(position)-gt))
        self.slam_errors.append(error)
        pose = np.eye(4, dtype=np.float32)
        from localization.orb_slam3 import quaternion_matrix
        pose[:3,:3] = quaternion_matrix(estimate['quaternion'])
        pose[:3,3] = position
        # Freeze sensor buffers once; consumers share immutable references.
        rgb, depth = rgb.copy(), depth.copy()
        with self.lock:
            if self._last_frame_wall is not None:
                self.frame_intervals.append(began-self._last_frame_wall)
            self._last_frame_wall = began
            self.rgb = rgb
            self.frame_seq += 1
            self.frame_time = began - getattr(sim, 'last_render_ms', 0)/1000.
            self.state['trajectory'].append(position)
            self._map_seq += 1
            if len(self.state['trajectory']) > 5000:
                del self.state['trajectory'][:1000]
            visual = self.state.get('visual_tracking')
            self.state.update(position=position, quaternion=estimate['quaternion'],
                              tracking='OK' if visual in ('OK', 'OK_KLT') else 'IMU_ONLY',
                              localization_source='30Hz Gaussian-noise IMU strapdown + asynchronous ORB-SLAM3 mapping',
                              slam_error_m=error, slam_mean_error_m=float(np.mean(self.slam_errors)),
                              missing_tracking_frames=0, steps=sim.steps, distance_m=sim.distance,
                              frame_timestamp=sim.timestamp, imu_samples=estimate['imu_samples'])
            packet = {'rgb': rgb, 'depth': depth, 'seq': self.frame_seq, 'captured_at': self.frame_time,
                      'timestamp': sim.timestamp, 'imu': imu, 'gt': gt.copy(), 'pose': pose,
                      'run_id': self.state.get('run_id'), 'generation': self.intent_generation}
            self._jpeg_packet = (rgb, overview, self.frame_seq, packet['run_id'])
            self._jpeg_event.set()
            self.stage_ms['render'].append(getattr(sim, 'last_render_ms', 0))
            self.stage_ms['frame'].append((time.perf_counter()-began)*1000)
        if self._workers:
            self._workers.submit(packet)
        self.log('localization', {'timestamp': sim.timestamp, 'estimate': position, 'gt': gt,
                                  'tracking': self.state['tracking'], 'error_m': error})
        return estimate

    def _recognition(self):
        recognizer, last_seq = None, -1
        while not self.exit_requested:
            time.sleep(.2)  # independent 5Hz maximum; does not block sensor clock
            with self.lock:
                seq, rgb = self.frame_seq, self.rgb
                if not self.state['has_started'] or rgb is None or seq == last_seq:
                    continue
                target, run_id, observed = self.state.get('target'), self.state.get('run_id'), self.frame_time
            try:
                if recognizer is None:
                    from recognition.client import RecognitionClient
                    recognizer = RecognitionClient()
                result = recognizer.detect(rgb, target)
                last_seq = seq
                with self.lock:
                    if self.state.get('run_id') != run_id or not self.state['has_started']:
                        continue
                    self.state.update(detections=clean(result.get('boxes', [])), detection_seq=seq,
                                      detection_time=time.time(), recognition_ms=result.get('latency_ms'),
                                      recognition_model=result.get('model'), recognition_error=None)
                    self.controller.recognition_done(observed)
                self.log('recognition', dict(result, frame_seq=seq, target=target), run_id=run_id)
            except Exception as exc:
                with self.lock:
                    if self.state.get('run_id') == run_id and self.state['has_started']:
                        self.update(recognition_error=str(exc))

    def _record_dispatch(self, decision):
        latency = (time.perf_counter()-decision['packet']['captured_at'])*1000
        with self.lock:
            self.e2e_latencies.append(latency)
            self.stage_ms['policy'].append(decision['request_ms'])
        self.log('navigation', dict(decision['info'], action=decision['action'],
                                   frame_seq=decision['packet']['seq'], e2e_ms=latency,
                                   step=self.state['steps']))

    def _engine_loop(self):
        sim = localizer = None
        catalog = None
        frames = decision = queued_decision = None
        deadline = time.perf_counter()
        try:
            while not self.exit_requested:
                try:
                    item = self.commands.get(timeout=0 if self.run_gate.is_set() else .1)
                    command, generation = item if isinstance(item, tuple) else (item, self.intent_generation)
                except queue.Empty:
                    command = None
                if command in ('reset', 'exit'):
                    if self._workers:
                        self._workers.close()
                    self._workers = None
                    if sim:
                        sim.close()
                    sim = localizer = frames = decision = queued_decision = None
                    self._write_summary()
                    with self.lock:
                        self.frame = self.overview = self.rgb = self._jpeg_packet = None
                        self._map_seq += 1
                        self.state.update(has_started=False, status='idle', steps=0, position=None,
                                          trajectory=[], map_points=[], detections=[], stop_reason=None, error=None,
                                          message='已释放仿真资源')
                    continue
                if command == 'start':
                    if generation != self.intent_generation:
                        continue
                    if sim is None:
                        self.update(status='loading', message='加载场景与模型…', error=None)
                        from .simulator import DroneSimulator, EpisodeCatalog
                        from localization import FusedLocalizer
                        from navigation import NavigationPolicy
                        catalog = catalog or EpisodeCatalog()
                        episode = catalog.choose()
                        self.run_dir = ROOT/'runs'/(time.strftime('%Y%m%d_%H%M%S')+'_'+str(time.time_ns()%1000000))
                        self.run_dir.mkdir(parents=True, exist_ok=True)
                        sim = DroneSimulator(episode)
                        (self.run_dir/'episode.json').write_text(json.dumps(clean(episode), indent=2))
                        (self.run_dir/'metadata.json').write_text(json.dumps(sim.metadata(), indent=2))
                        localizer = FusedLocalizer()
                        position, q = sim.camera_pose()
                        localizer.reset(position, q[[3,0,1,2]])
                        self._inertial = IdealIMUIntegrator()
                        self._inertial.reset(position, q[[3,0,1,2]])
                        policy = NavigationPolicy()
                        policy.reset(episode['object_category'])
                        self.e2e_latencies.clear()
                        self.frame_intervals.clear()
                        for measurements in self.stage_ms.values():
                            measurements.clear()
                        self.slam_errors = []
                        self._last_frame_wall = None
                        self.controller.clear_metrics()
                        self.update(**sim.metadata(), has_started=True, stop_reason=None, detections=[], trajectory=[],
                                    map_points=[], run_id=self.run_dir.name, navigation_model='InternVLA-N1-DualVLN',
                                    mapping_error=None, navigation_error=None, recognition_error=None, visual_tracking=None)
                        self._workers = EpisodeWorkers(self, localizer, policy, generation)
                        self._new_frame(sim, *sim.observe(), localizer)
                    if generation != self.intent_generation:
                        continue
                    if sim.steps < 500 and self.state.get('stop_reason') != 'MODEL_STOP':
                        self.run_gate.set()
                        self.controller.set('HOLD')
                        self.update(status='running', message='实时仿真运行中', stop_reason=None)
                        deadline = time.perf_counter()
                if not self.run_gate.is_set() or sim is None:
                    frames = decision = queued_decision = None
                    continue
                received = self._workers.take(allow_motion=queued_decision is None)
                if received and received['packet']['generation'] == self.intent_generation:
                    if received['action'] == 'STOP':
                        self._record_dispatch(received)
                        self.run_gate.clear()
                        self.controller.pause()
                        self.update(status='paused', stop_reason='MODEL_STOP', current_action='STOP',
                                    message='模型输出 STOP，已自动暂停')
                        self._write_summary()
                        continue
                    queued_decision = received
                with self.lock:
                    if frames is None and queued_decision is not None:
                        decision, queued_decision = queued_decision, None
                        if decision['packet']['generation'] != self.intent_generation or not self.run_gate.is_set():
                            continue
                        action = decision['action']
                        self.event('动作 %03d · %s' % (sim.steps+1, action))
                        if decision['packet']['generation'] != self.intent_generation or not self.run_gate.is_set():
                            continue
                        self.controller.set(action)
                        self.update(current_action=action, policy=decision['info'], message='执行 '+action)
                        frames = sim.advance(action)
                        remaining_frames = 8
                    if not self.run_gate.is_set():
                        continue
                    capture_begin = time.perf_counter()
                    if frames is not None:
                        try:
                            frame = next(frames)
                            if decision is not None:
                                self._record_dispatch(decision)
                                decision = None
                            remaining_frames -= 1
                            if remaining_frames == 0:
                                frames = None
                        except StopIteration:
                            frames = None
                            frame = sim.stationary_frame()
                    else:
                        if self.controller.command != 'HOLD':
                            self.controller.set('HOLD')
                        frame = sim.stationary_frame()
                self._new_frame(sim, *frame, localizer)
                if sim.steps >= 500 and frames is None:
                    self.run_gate.clear()
                    self.controller.pause()
                    self.update(status='paused', stop_reason='STEP_LIMIT', message='达到 500 步上限，已自动暂停')
                    self._write_summary()
                deadline += self.frame_period
                wait = deadline-time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
                elif wait < -self.frame_period:
                    deadline = time.perf_counter()
        except Exception as exc:
            traceback.print_exc()
            self.run_gate.clear()
            self.controller.pause()
            self.update(status='error', error=str(exc), message='运行中断')
            self._write_summary()
        finally:
            if self._workers:
                self._workers.close()
                self._workers = None
            if sim:
                sim.close()


class Handler(BaseHTTPRequestHandler):
    platform = None

    def send_bytes(self, status, body=b'', content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/state':
            return self.send_bytes(200, json.dumps(self.platform.snapshot(), ensure_ascii=False).encode())
        if path == '/api/map':
            return self.send_bytes(200, json.dumps(self.platform.map_snapshot(), ensure_ascii=False).encode())
        if path == '/api/stream.mjpg':
            from urllib.parse import parse_qs
            query = parse_qs(urlparse(self.path).query)
            view = query.get('view', ['front'])[0]
            with self.platform.lock:
                run_id = query.get('run', [self.platform.state.get('run_id')])[0]
            def invalid():
                return (self.platform.exit_requested or not self.platform.state['has_started'] or
                        self.platform.state.get('run_id') != run_id or
                        self.platform.state.get('status') in ('resetting', 'exited'))
            def body_now():
                return self.platform.overview if view == 'overview' else self.platform.frame
            with self.platform.lock:
                if invalid():
                    return self.send_bytes(204, b'', 'image/jpeg')
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            last = -1
            try:
                while not self.platform.exit_requested:
                    with self.platform.stream_condition:
                        self.platform.stream_condition.wait_for(
                            lambda: invalid() or (body_now() is not None and
                                                  self.platform.state.get('jpeg_seq', -1) != last),
                            timeout=1.)
                        if invalid():
                            break
                        seq = self.platform.state.get('jpeg_seq', -1)
                        body = body_now()
                        if body is None or seq == last:
                            continue
                        last = seq
                    self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(body)).encode()+
                                     b'\r\nX-Frame-Seq: '+str(seq).encode()+b'\r\n\r\n'+body+b'\r\n')
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        if path in ('/api/frame.jpg', '/api/overview.jpg'):
            with self.platform.lock:
                body = self.platform.frame if 'frame' in path else self.platform.overview
            return self.send_bytes(200 if body else 204, body or b'', 'image/jpeg')
        if path == '/api/health':
            return self.send_bytes(200, json.dumps({'ready': True, 'status': self.platform.state['status']}).encode())
        relative = 'index.html' if path == '/' else path.lstrip('/')
        asset = (ROOT / 'web' / relative).resolve()
        if '..' in Path(relative).parts or (ROOT / 'web').resolve() not in asset.parents:
            return self.send_bytes(403)
        if asset.is_file():
            return self.send_bytes(200, asset.read_bytes(), mimetypes.guess_type(str(asset))[0] or 'application/octet-stream')
        self.send_bytes(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith('/api/'):
            try:
                self.platform.command(path.split('/')[-1])
                exiting = path == '/api/exit'
                self.send_bytes(202, b'{"accepted":true,"status":"exiting"}' if exiting else b'{"accepted":true}')
                if exiting:
                    with self.platform.lock:
                        if self.platform._shutdown_scheduled:
                            return
                        self.platform._shutdown_scheduled = True
                    def shutdown():
                        time.sleep(.5)
                        from scripts.manage import request_shutdown
                        request_shutdown(ROOT)
                    threading.Thread(target=shutdown, daemon=True, name='supervisor-exit-request').start()
                return
            except ValueError:
                pass
        self.send_bytes(404)

    def log_message(self, *args):
        pass


def main():
    cv2.setNumThreads(1)
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', default=5800, type=int)
    args = parser.parse_args()
    Handler.platform = Platform()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print('DRONENAV_UI_READY http://%s:%d' % (args.host, args.port), flush=True)
    try:
        server.serve_forever()
    finally:
        Handler.platform.command('exit')
        server.server_close()


if __name__ == '__main__':
    main()
