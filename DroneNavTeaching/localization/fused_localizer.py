"""IMU strapdown pose and a real ORB-SLAM3 RGB-D map.

Only reset receives the initial world pose. Subsequent noisy IMU readings are
integrated without absolute ground truth or a visual position correction.
Visual poses build independently aligned maps. Inertial drift is evaluated,
not clamped or replaced with an artificial displayed error.
"""
import time
import numpy as np
from .ideal_imu import IdealIMUIntegrator
from .orb_slam3 import ORBSLAM3Localizer, quaternion_matrix, matrix_quaternion

class FusedLocalizer:
    def __init__(self, project_dir=None, timeout=60.):
        self.visual=ORBSLAM3Localizer(project_dir=project_dir,timeout=timeout)
        self.inertial=IdealIMUIntegrator()
        self._map_alignments={}
        self._map_points={}
        self.frames=0;self.visual_tracked_frames=0
        self.last_visual=None
        self._last_timestamp=None

    def reset(self, initial_camera_position, initial_camera_rotation, initial_velocity=None):
        self.inertial.reset(initial_camera_position,initial_camera_rotation,initial_velocity)
        self._map_alignments={};self._map_points={};self.frames=self.visual_tracked_frames=0;self.last_visual=None;self._last_timestamp=None
        return self.visual.reset(initial_camera_position,initial_camera_rotation)

    def track(self, rgb, depth, timestamp, imu=None):
        start=time.perf_counter()
        if self._last_timestamp is not None and float(timestamp)<=self._last_timestamp:raise ValueError('Timestamps must increase')
        if imu is not None:
            if isinstance(imu,list):
                for sample in imu:self.inertial.update(sample['gyro_body_rad_s'],sample['specific_force_body_m_s2'],sample['dt'])
            else:self.inertial.update(imu['gyro_body_rad_s'],imu['specific_force_body_m_s2'],imu['dt'])
        elif self.frames:
            raise ValueError('Every moving or stationary interval requires an IMU sample; only the initial image may omit it')
        self._last_timestamp=float(timestamp);self.frames+=1
        inertial=self.inertial.state()
        visual=self.visual.track(rgb,depth,timestamp);self.last_visual=visual
        state=visual['raw_tracking'];valid=state in ('OK','OK_KLT')
        pose=np.eye(4);pose[:3,3]=self.inertial.position;pose[:3,:3]=self.inertial.rotation@np.diag([1.,-1.,-1.])
        raw=np.asarray(visual['slam_Twc'],dtype=float).reshape(4,4);mid=visual['map_id'];reanchored=False
        if valid and mid not in self._map_alignments:
            # A newly initialized ORB map has no world origin; inertial pose
            # supplies this transform. There is no access to ground truth here.
            self._map_alignments[mid]=pose@np.linalg.inv(raw);reanchored=True
        points=[];visual_position=None;innovation=None;consistent=None
        if mid in self._map_alignments:
            alignment=self._map_alignments[mid]
            points_raw=np.asarray(visual['slam_map_points'],dtype=float).reshape(-1,3)
            points=(points_raw@alignment[:3,:3].T+alignment[:3,3]).tolist()
            if valid:
                estimate=alignment@raw;visual_position=estimate[:3,3].tolist()
                innovation=float(np.linalg.norm(estimate[:3,3]-self.inertial.position));consistent=innovation<=.1
        if points:self._map_points[mid]=points
        # Keep the last mapped geometry visible during visual dropout/new-map initialization.
        combined=[p for cloud in self._map_points.values() for p in cloud]
        if len(combined)>5000:combined=combined[::int(np.ceil(len(combined)/5000))]
        points=combined
        if valid:self.visual_tracked_frames+=1
        latency=(time.perf_counter()-start)*1000
        return dict(position=inertial['position'],quaternion=inertial['quaternion'],velocity=inertial['velocity'],
                    tracking='OK' if valid else 'IMU_ONLY',visual_tracking=state,
                    map_points=points,map_point_count=visual['map_point_count'],tracked_point_count=visual['tracked_point_count'],
                    map_id=mid,mapped_submaps=len(self._map_points),display_map_point_count=len(points),map_reanchored_to_inertial=reanchored,visual_position=visual_position,
                    visual_innovation_m=innovation,visual_consistent=consistent,visual_kalman_gain=0.0,
                    latency_ms=latency,latency=latency,orb_latency_ms=visual['orb_latency_ms'],
                    frequency_hz=1000/latency if latency else 0,total_frames=self.frames,
                    tracked_frames=self.frames,coverage=1.,visual_coverage=self.visual_tracked_frames/self.frames,
                    imu_samples=inertial['imu_samples'],source='IMU strapdown + ORB-SLAM3 RGB-D mapping',
                    assumptions=['exact initial pose and zero initial velocity','IMU readings integrated as supplied; no absolute correction','semi-implicit discrete IMU sampling contract'])

    def close(self):self.visual.close()
