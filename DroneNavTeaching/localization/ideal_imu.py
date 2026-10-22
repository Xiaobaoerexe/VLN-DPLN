"""Discrete IMU strapdown for the teaching simulator's sensor readings.

Frame: Habitat body/camera, x right/y up/z backward. Gravity is world -y.
Samples express angular rate and specific force in the interval END body frame.
The simulator uses semi-implicit integration: rotate, accelerate, update v, update p.
This is an explicit discrete sensor contract, not a continuous-time IMU benchmark.
Only reset accepts absolute position/orientation; update accepts IMU readings only.
"""
import numpy as np
from .orb_slam3 import quaternion_matrix, matrix_quaternion

GRAVITY=np.array([0.,-9.80665,0.])

def rotation_exp(v):
    v=np.asarray(v,dtype=float);angle=float(np.linalg.norm(v))
    K=np.array([[0.,-v[2],v[1]],[v[2],0.,-v[0]],[-v[1],v[0],0.]])
    if angle<1e-8:return np.eye(3)+K+.5*(K@K)
    return np.eye(3)+(np.sin(angle)/angle)*K+((1-np.cos(angle))/(angle*angle))*(K@K)

class IdealIMUIntegrator:
    def reset(self, initial_camera_position, initial_camera_rotation, initial_velocity=None):
        self.position=np.asarray(initial_camera_position,dtype=float).copy()
        self.rotation=quaternion_matrix(initial_camera_rotation)
        self.velocity=np.zeros(3) if initial_velocity is None else np.asarray(initial_velocity,dtype=float).copy()
        self.elapsed=0.;self.samples=0
        return self.state()

    def update(self, gyro_body_rad_s, specific_force_body_m_s2, dt):
        dt=float(dt)
        if not 0<dt<=1.:raise ValueError('IMU dt must be in (0,1] seconds')
        gyro=np.asarray(gyro_body_rad_s,dtype=float).reshape(3)
        force=np.asarray(specific_force_body_m_s2,dtype=float).reshape(3)
        if not np.all(np.isfinite(gyro)) or not np.all(np.isfinite(force)):raise ValueError('IMU readings must be finite')
        self.rotation=self.rotation@rotation_exp(gyro*dt)
        acceleration=self.rotation@force+GRAVITY
        self.velocity+=acceleration*dt
        self.position+=self.velocity*dt
        self.elapsed+=dt;self.samples+=1
        # Periodic projection prevents numerical orthogonality drift.
        if self.samples%1000==0:
            u,_,vt=np.linalg.svd(self.rotation);self.rotation=u@vt
        return self.state()

    def state(self):
        return dict(position=self.position.tolist(),quaternion=matrix_quaternion(self.rotation),velocity=self.velocity.tolist(),imu_samples=self.samples,imu_elapsed_s=self.elapsed)
