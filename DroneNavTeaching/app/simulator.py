"""Read-only HM3D-OVON adapter. No target locations enter the navigation policy."""
import gzip
import json
import math
import os
import random
import time
from pathlib import Path

import cv2
import habitat_sim
import numpy as np
import quaternion
from .imu_sensor import GaussianIMUSensor

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv('DRONENAV_DATA_ROOT', str(ROOT/'data' if (ROOT/'data').exists()
                                            else Path('/home/abc/Documents/VLN/ovon/data'))))
CONTENT = DATA / 'datasets/ovon/hm3d/val_unseen/content'
CAMERA_HEIGHT = 1.4
WIDTH, HEIGHT = 640, 480


class EpisodeCatalog:
    def __init__(self):
        self.files = sorted(p for p in CONTENT.glob('*.json.gz') if not p.name.startswith('._'))
        if len(self.files) != 36:
            raise RuntimeError('Expected 36 OVON scenes, found %d' % len(self.files))
        self.cache = {}

    def choose(self, seed=None, scene_index=None, episode_index=None):
        rng = random.Random(seed)
        path = self.files[scene_index] if scene_index is not None else rng.choice(self.files)
        if path not in self.cache:
            with gzip.open(path, 'rt') as stream:
                self.cache[path] = json.load(stream)['episodes']
        episodes = self.cache[path]
        ep = episodes[episode_index] if episode_index is not None else rng.choice(episodes)
        return dict(ep)


class DroneSimulator:
    """Fixed-altitude, noise-free, NavMesh-constrained kinematic drone.

    One OVON primitive is 0.25m or 10 degrees. Eight rendered intermediate
    observations make those primitives continuous enough for visual SLAM.
    """
    def __init__(self, episode, seed=0, render_overview=True):
        self.episode = episode
        scene = DATA / 'scene_datasets' / episode['scene_id'].replace('.basis.glb', '.glb')
        if not scene.exists():
            raise FileNotFoundError(str(scene))
        cfg = habitat_sim.SimulatorConfiguration()
        cfg.scene_id = str(scene)
        cfg.scene_dataset_config_file = str(DATA / 'scene_datasets/hm3d/hm3d_annotated_basis.scene_dataset_config.json')
        cfg.enable_physics = True
        cfg.gpu_device_id = 0
        cfg.random_seed = seed
        sensors = []
        for name, kind in [('rgb', habitat_sim.SensorType.COLOR), ('depth', habitat_sim.SensorType.DEPTH)]:
            spec = habitat_sim.CameraSensorSpec()
            spec.uuid, spec.sensor_type = name, kind
            spec.resolution, spec.position, spec.hfov = [HEIGHT, WIDTH], [0, CAMERA_HEIGHT, 0], 90
            sensors.append(spec)
        if render_overview:
            spec = habitat_sim.CameraSensorSpec()
            spec.uuid, spec.sensor_type = 'overview', habitat_sim.SensorType.COLOR
            spec.resolution = [240, 320]
            spec.position, spec.orientation, spec.hfov = [0, 1.9, 1.35], [-math.atan2(.5, .95), 0, 0], 80
            sensors.append(spec)
        agent_cfg = habitat_sim.agent.AgentConfiguration()
        agent_cfg.height, agent_cfg.radius = 1.4, .18
        agent_cfg.sensor_specifications = sensors
        self.sim = habitat_sim.Simulator(habitat_sim.Configuration(cfg, [agent_cfg]))
        self.agent = self.sim.initialize_agent(0)
        if not self.sim.pathfinder.is_loaded:
            settings = habitat_sim.NavMeshSettings()
            settings.set_defaults()
            settings.agent_radius = .18
            if not self.sim.recompute_navmesh(self.sim.pathfinder, settings):
                raise RuntimeError('Cannot construct NavMesh')
        state = self.agent.get_state()
        original = np.asarray(episode['start_position'], dtype=np.float32)
        snapped = np.asarray(self.sim.pathfinder.snap_point(original), dtype=np.float32)
        self.snap_error = float(np.linalg.norm(snapped - original))
        if not np.isfinite(snapped).all() or self.snap_error > .25:
            raise ValueError('Invalid episode start, snap error %.3fm' % self.snap_error)
        state.position = snapped
        rot = episode['start_rotation']
        state.rotation = np.quaternion(rot[3], rot[0], rot[1], rot[2])
        self.agent.set_state(state)
        self.floor_y = float(snapped[1])
        self.timestamp = 0.
        self.steps = 0
        self.distance = 0.
        self.imu = GaussianIMUSensor(*self.camera_pose(), seed=seed)
        self.last_imu = None
        self._observation = None
        self.last_render_ms = 0.
        self.drone = None
        self._add_drone()

    def _add_drone(self):
        asset = ROOT / 'assets/iris.glb'
        if not asset.exists():
            return
        manager = self.sim.get_object_template_manager()
        attrs = habitat_sim.attributes.ObjectAttributes()
        attrs.render_asset_handle = str(asset)
        attrs.scale = [1., 1., 1.]
        attrs.is_collidable = False
        handle = manager.register_template(attrs, 'teaching_iris')
        self.drone = self.sim.get_rigid_object_manager().add_object_by_template_id(handle)
        self.drone.motion_type = habitat_sim.physics.MotionType.KINEMATIC
        self._move_drone()

    def _move_drone(self):
        if self.drone is None:
            return
        import magnum as mn
        state = self.agent.get_state()
        offset = quaternion.rotate_vectors(state.rotation, np.array([0., CAMERA_HEIGHT, .40]))
        self.drone.translation = mn.Vector3(np.asarray(state.position) + offset)
        q = state.rotation
        self.drone.rotation = mn.Quaternion(mn.Vector3(q.x, q.y, q.z), q.w)

    def observe(self):
        if self._observation is not None:
            return self._observation
        began = time.perf_counter()
        obs = self.sim.get_sensor_observations()
        self.last_render_ms = (time.perf_counter() - began) * 1000
        self._observation = (np.ascontiguousarray(obs['rgb'][..., :3]), np.asarray(obs['depth'], dtype=np.float32),
                             np.ascontiguousarray(obs['overview'][..., :3]) if 'overview' in obs else None)
        return self._observation

    def stationary_frame(self, dt=1. / 30.):
        """Advance the sensor clock at rest, without consuming an OVON action."""
        self.timestamp += dt
        self.last_imu = self.imu.sample(*self.camera_pose(), dt=dt)
        self._observation = None
        return self.observe()

    def camera_pose(self):
        sensor = self.agent.get_state().sensor_states['rgb']
        q = sensor.rotation
        return np.asarray(sensor.position, dtype=float), np.array([q.x, q.y, q.z, q.w], dtype=float)

    def metadata(self):
        return {'scene': Path(self.episode['scene_id']).parent.name,
                'episode_id': str(self.episode['episode_id']), 'target': self.episode['object_category'],
                'scene_count': 36, 'camera_height_m': CAMERA_HEIGHT, 'drone_height_m': CAMERA_HEIGHT,
                'navigable_area_m2': float(self.sim.pathfinder.navigable_area),
                'start_snap_m': self.snap_error, 'motion': 'noise-free fixed-altitude kinematic; NavMesh collision constraint',
                'asset_loaded': self.drone is not None, 'imu_noise': self.imu.noise_metadata()}

    def advance(self, action, substeps=8):
        if self.steps >= 500:
            raise RuntimeError('500-step episode budget exhausted')
        if action not in ('FORWARD', 'LEFT', 'RIGHT'):
            raise ValueError('Unexpected motion action: %s' % action)
        self.steps += 1
        for index in range(substeps):
            state = self.agent.get_state()
            previous = np.asarray(state.position).copy()
            if action == 'FORWARD':
                direction = quaternion.rotate_vectors(state.rotation, np.array([0., 0., -1.]))
                wanted = previous + direction * (.25 / substeps)
                allowed = np.asarray(self.sim.pathfinder.try_step(previous, wanted))
                # Fixed elevation, even at stairs; floor transitions are excluded.
                if np.isfinite(allowed).all() and abs(float(allowed[1]) - self.floor_y) < .04:
                    allowed[1] = self.floor_y
                    state.position = allowed
            else:
                radians = math.radians((10. if action == 'LEFT' else -10.) / substeps)
                state.rotation = np.quaternion(math.cos(radians / 2), 0, math.sin(radians / 2), 0) * state.rotation
            self.agent.set_state(state)
            self.distance += float(np.linalg.norm(np.asarray(state.position) - previous))
            self._move_drone()
            self.timestamp += 1. / 30.
            self.last_imu = self.imu.sample(*self.camera_pose(), dt=1. / 30.)
            self._observation = None
            yield self.observe()

    def close(self):
        self.sim.close()
