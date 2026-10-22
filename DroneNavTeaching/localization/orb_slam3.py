"""Real ORB-SLAM3 RGB-D worker, with one initial Habitat-world alignment.

Only reset() accepts ground truth. track() accepts images and time exclusively.
Quaternion convention is w,x,y,z. World axes follow Habitat; optical axes are
right/down/forward. No simulated IMU or privileged pose enters visual tracking.
"""
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import threading
import time
import uuid
import numpy as np

STATE_NAMES = {0: 'NO_IMAGES_YET', 1: 'NOT_INITIALIZED', 2: 'OK', 3: 'RECENTLY_LOST', 4: 'LOST', 5: 'OK_KLT'}

def quaternion_matrix(q):
    q = np.asarray(q, dtype=float).reshape(4)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])

def matrix_quaternion(m):
    # Symmetric eigenproblem is stable near 180 degrees.
    r = np.asarray(m, dtype=float)
    K = np.array([[r[0,0]-r[1,1]-r[2,2],r[0,1]+r[1,0],r[0,2]+r[2,0],r[2,1]-r[1,2]],
                  [r[0,1]+r[1,0],r[1,1]-r[0,0]-r[2,2],r[1,2]+r[2,1],r[0,2]-r[2,0]],
                  [r[0,2]+r[2,0],r[1,2]+r[2,1],r[2,2]-r[0,0]-r[1,1],r[1,0]-r[0,1]],
                  [r[2,1]-r[1,2],r[0,2]-r[2,0],r[1,0]-r[0,1],np.trace(r)]])/3
    vals, vecs = np.linalg.eigh(K)
    xyzw=vecs[:,np.argmax(vals)]
    q=xyzw[[3,0,1,2]]
    return (q if q[0]>=0 else -q).tolist()

class ORBSLAM3Localizer:
    def __init__(self, project_dir=None, timeout=60.0):
        self.root = Path(project_dir or Path(__file__).resolve().parents[1])
        self.timeout = timeout
        self.process = None
        self._lock = threading.Lock()
        self._log = None
        self._alignment = None
        self._map_id = None
        self.frames = 0
        self.tracked_frames = 0
        self._last_timestamp = None
        self.last_position = None

    def _read_exact(self, n):
        out=bytearray()
        deadline=time.monotonic()+self.timeout
        while len(out)<n:
            remaining=deadline-time.monotonic()
            if remaining<=0: raise TimeoutError('ORB-SLAM3 worker timed out; inspect '+str(self.log_path))
            ready,_,_=select.select([self.process.stdout],[],[],remaining)
            if not ready: continue
            chunk=os.read(self.process.stdout.fileno(),n-len(out))
            if not chunk: raise RuntimeError('ORB-SLAM3 worker exited; inspect '+str(self.log_path))
            out.extend(chunk)
        return bytes(out)

    def _message(self):
        magic=self._read_exact(4)
        if magic!=b'OSR3': raise RuntimeError('Invalid ORB IPC response '+repr(magic))
        size=struct.unpack('<I',self._read_exact(4))[0]
        if size>10_000_000: raise RuntimeError('Unreasonable ORB IPC response size')
        return json.loads(self._read_exact(size))

    def reset(self, initial_camera_position, initial_camera_rotation):
        """Start a fresh map, using one initial Habitat camera pose (wxyz)."""
        with self._lock:
            self._close()
            self._initial = np.eye(4)
            self._initial[:3,3]=np.asarray(initial_camera_position,dtype=float)
            self._initial[:3,:3]=quaternion_matrix(initial_camera_rotation) @ np.diag([1.,-1.,-1.])
            self._alignment=None
            self._map_id=None
            self.frames=self.tracked_frames=0
            self.last_position=None
            self._last_timestamp=None
            log_dir=self.root/'logs';log_dir.mkdir(exist_ok=True)
            self.log_path=log_dir/('orb-'+uuid.uuid4().hex[:10]+'.log')
            self._log=open(self.log_path,'wb',buffering=0)
            env=os.environ.copy()
            env['LD_LIBRARY_PATH']=':'.join(str(p) for p in [self.root/'vendor/ORB_SLAM3/lib',self.root/'vendor/ORB_SLAM3/Thirdparty/DBoW2/lib',self.root/'vendor/ORB_SLAM3/Thirdparty/g2o/lib',self.root/'vendor/pangolin-install/lib'])
            env['OMP_NUM_THREADS']='1';env['OPENBLAS_NUM_THREADS']='1'
            self.process=subprocess.Popen([str(self.root/'localization/build/orb_ipc'),str(self.root/'vendor/ORB_SLAM3/Vocabulary/ORBvoc.txt'),str(self.root/'localization/habitat_rgbd.yaml')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self._log,bufsize=0,env=env)
            return self._message()

    def track(self, rgb, depth, timestamp):
        """Track 640x480 RGB uint8 / depth float32 metres, without ground truth."""
        with self._lock:
            if self.process is None: raise RuntimeError('Call reset(initial_camera_position, initial_camera_rotation) first')
            if self._last_timestamp is not None and float(timestamp)<=self._last_timestamp:
                raise ValueError('ORB-SLAM3 timestamps must strictly increase')
            color=np.ascontiguousarray(np.asarray(rgb)[:,:,:3],dtype=np.uint8)
            depth=np.ascontiguousarray(np.asarray(depth).reshape(color.shape[:2]),dtype='<f4')
            if color.shape!=(480,640,3): raise ValueError('Calibrated camera is 640x480 HFOV90; received '+str(color.shape))
            depth=depth.copy();depth[(~np.isfinite(depth))|(depth<=0)]=0
            start=time.perf_counter()
            request=struct.pack('<4sIId',b'OSF3',640,480,float(timestamp))+color.tobytes()+depth.tobytes()
            payload=memoryview(request)
            while payload:
                written=self.process.stdin.write(payload)
                if not written: raise RuntimeError('ORB worker input pipe closed')
                payload=payload[written:]
            self.process.stdin.flush()
            raw=self._message()
            self._last_timestamp=float(timestamp)
            self._last_raw=raw
            self.frames+=1
            state=STATE_NAMES.get(raw['state'],'UNKNOWN')
            Twc=np.asarray(raw['Twc'],dtype=float).reshape(4,4)
            valid=state in ('OK','OK_KLT') and np.isfinite(Twc).all()
            if valid and self._alignment is None:
                self._alignment=self._initial @ np.linalg.inv(Twc)
                self._map_id=raw['map_id']
            if self._map_id is not None and raw['map_id']!=self._map_id:
                valid=False;state='UNALIGNED_MAP'
            position=quaternion=None
            points=[]
            if valid:
                pose=self._alignment@Twc
                position=pose[:3,3].tolist();quaternion=matrix_quaternion(pose[:3,:3] @ np.diag([1.,-1.,-1.]))
                self.last_position=position
                self.tracked_frames+=1
            if self._alignment is not None and raw['map_id']==self._map_id:
                pts=np.asarray(raw['points'],dtype=float).reshape(-1,3)
                points=(pts@self._alignment[:3,:3].T+self._alignment[:3,3]).tolist()
            latency_ms=(time.perf_counter()-start)*1000
            return dict(position=position,quaternion=quaternion,tracking=state,map_points=points,slam_Twc=raw['Twc'],slam_map_points=raw['points'],raw_tracking=STATE_NAMES.get(raw['state'],'UNKNOWN'),
                        map_point_count=raw['map_point_count'],tracked_point_count=raw['tracked_point_count'],
                        latency_ms=latency_ms,latency=latency_ms,orb_latency_ms=raw['latency_ms'],
                        frequency_hz=1000/latency_ms if latency_ms else 0,map_id=raw['map_id'],
                        tracked_frames=self.tracked_frames,total_frames=self.frames,
                        coverage=self.tracked_frames/self.frames,source='ORB-SLAM3 RGB-D')

    def _close(self):
        if self.process is not None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=10)
            except Exception:
                self.process.kill();self.process.wait()
            self.process=None
        if self._log is not None: self._log.close();self._log=None

    def close(self):
        with self._lock:self._close()

    def __del__(self):
        try:self._close()
        except Exception:pass
