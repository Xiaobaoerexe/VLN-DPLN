"""Evaluate sensor noise and strapdown drift without loading GPU models."""
import argparse
import json
import math
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.imu_sensor import GaussianIMUSensor
from localization.ideal_imu import IdealIMUIntegrator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, default=16)
    args = parser.parse_args()
    started = time.perf_counter()
    dt = 1. / 30.
    origin, identity = [0., 1.4, 0.], [0., 0., 0., 1.]
    sensor = GaussianIMUSensor(origin, identity, seed=971)
    readings = [sensor.sample(origin, identity, dt) for _ in range(20000)]
    accel = np.asarray([x['specific_force_body_m_s2'] for x in readings]) - [0., 9.80665, 0.]
    gyro = np.asarray([x['gyro_body_rad_s'] for x in readings])
    distribution = {}
    for name, values, std in [('accel', accel, sensor.accel_std), ('gyro', gyro, sensor.gyro_std)]:
        distribution[name] = dict(expected_std=std, means=values.mean(0).tolist(), stds=values.std(0).tolist())
        assert np.all(np.abs(values.mean(0)) < .04 * std)
        assert np.all(np.abs(values.std(0) / std - 1.) < .04)
    actions = ((['FORWARD'] * 32 + ['LEFT'] * 9) * 4 * 4)[:500]
    results = []
    for seed in range(args.seeds):
        position = np.asarray(origin, dtype=float)
        yaw = 0.
        sensor = GaussianIMUSensor(position, identity, seed=seed)
        estimator = IdealIMUIntegrator()
        estimator.reset(position, [1., 0., 0., 0.])
        errors, positions = [], []
        def observe():
            q = [0., math.sin(yaw / 2), 0., math.cos(yaw / 2)]
            sample = sensor.sample(position, q, dt)
            estimate = estimator.update(sample['gyro_body_rad_s'], sample['specific_force_body_m_s2'], dt)
            errors.append(float(np.linalg.norm(np.asarray(estimate['position']) - position)))
            positions.append(position.copy())
        for action in actions:
            for _ in range(8):
                if action == 'FORWARD':
                    position += np.asarray([-math.sin(yaw), 0., -math.cos(yaw)]) * (.25 / 8)
                else:
                    yaw += math.radians(10. / 8)
                observe()
            # Include 0.4s of sensor observations while semantic planning waits.
            for _ in range(12):
                observe()
        span = np.ptp(np.asarray(positions)[:, [0, 2]], axis=0)
        result = dict(seed=seed, actions=len(actions), samples=len(errors), simulation_seconds=len(errors) * dt,
                      trajectory_bbox_area_m2=float(np.prod(span)), mean_error_m=float(np.mean(errors)),
                      max_error_m=float(np.max(errors)), final_error_m=errors[-1])
        assert result['trajectory_bbox_area_m2'] >= 50.
        assert 0. < result['mean_error_m'] <= .1, result
        results.append(result)
    report = dict(passed=True, noise=sensor.noise_metadata(), distribution=distribution, runs=results,
                  duration_s=time.perf_counter() - started,
                  scope='Synthetic 500-action 64m2 trajectory with 333s sensor time; no post-init absolute pose correction. Not a fresh ORB/scene benchmark.')
    (ROOT / 'reports/noisy_imu_verification.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
