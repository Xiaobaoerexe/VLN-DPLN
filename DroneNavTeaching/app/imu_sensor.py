"""Ideal discrete IMU sensor for the noise-free kinematic simulator.

    Simulator state is used here to synthesize accelerometer and gyroscope
    measurements, as in a simulated physical IMU. Only specific force, angular
    rate and sampling interval leave this sensor; no position/velocity does.
    The estimator is a separate module with no access to the simulator.
"""
import numpy as np
import quaternion


class IdealIMUSensor:
    def __init__(self, position, rotation_xyzw):
        self.position = np.asarray(position, dtype=np.float64).copy()
        x, y, z, w = rotation_xyzw
        self.rotation = np.quaternion(w, x, y, z).normalized()
        self.velocity = np.zeros(3, dtype=np.float64)

    def sample(self, position, rotation_xyzw, dt):
        if dt <= 0:
            raise ValueError('IMU dt must be positive')
        position = np.asarray(position, dtype=np.float64)
        x, y, z, w = rotation_xyzw
        rotation = np.quaternion(w, x, y, z).normalized()
        velocity = (position - self.position) / dt
        acceleration = (velocity - self.velocity) / dt
        gravity = np.array([0., -9.80665, 0.])
        force_body = quaternion.rotate_vectors(rotation.inverse(), acceleration - gravity)
        delta_rotation = (self.rotation.inverse() * rotation).normalized()
        if delta_rotation.w < 0:
            delta_rotation = -delta_rotation
        gyro_body = quaternion.as_rotation_vector(delta_rotation) / dt
        self.position, self.rotation, self.velocity = position.copy(), rotation, velocity
        return {'gyro_body_rad_s': gyro_body.tolist(),
                'specific_force_body_m_s2': force_body.tolist(), 'dt': float(dt)}


class GaussianIMUSensor(IdealIMUSensor):
    """Independent zero-mean Gaussian measurement noise, with no bias.

    Standard deviations are per axis and per sample, not noise densities.
    The simulator's motion and the evaluator's ground truth remain unchanged.
    """
    ACCEL_STD_M_S2 = 1e-4
    GYRO_STD_RAD_S = 1e-8

    def __init__(self, position, rotation_xyzw, seed=0,
                 accel_std_m_s2=ACCEL_STD_M_S2, gyro_std_rad_s=GYRO_STD_RAD_S):
        super().__init__(position, rotation_xyzw)
        self.accel_std = float(accel_std_m_s2)
        self.gyro_std = float(gyro_std_rad_s)
        if not np.isfinite([self.accel_std, self.gyro_std]).all() or min(self.accel_std, self.gyro_std) < 0:
            raise ValueError('IMU noise standard deviations must be finite and nonnegative')
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)

    def sample(self, position, rotation_xyzw, dt):
        measured = super().sample(position, rotation_xyzw, dt)
        measured['gyro_body_rad_s'] = (np.asarray(measured['gyro_body_rad_s']) +
                                      self.rng.normal(0., self.gyro_std, 3)).tolist()
        measured['specific_force_body_m_s2'] = (np.asarray(measured['specific_force_body_m_s2']) +
                                              self.rng.normal(0., self.accel_std, 3)).tolist()
        return measured

    def noise_metadata(self):
        return dict(distribution='independent zero-mean Gaussian per axis per sample',
                    accel_std_m_s2=self.accel_std, gyro_std_rad_s=self.gyro_std,
                    seed=self.seed, bias=False)
