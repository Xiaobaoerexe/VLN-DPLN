"""Regression of the real asynchronous Platform using lightweight peripherals.

No running model or UI service is contacted. The test clock is accelerated;
the 50Hz/30Hz real performance is measured by profile_runtime.py separately.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DRONENAV_FRAME_HZ'] = '10000'
import numpy as np
from app import server


def wait(predicate, timeout=10):
    end = time.perf_counter()+timeout
    while not predicate():
        if time.perf_counter()>end:
            raise TimeoutError('Regression condition timed out')
        time.sleep(.002)


def main():
    stats = {'policy_calls': 0, 'motions': 0, 'imu_processed': 0, 'mode': 'FORWARD',
             'block_at': 3, 'inference_entered': threading.Event(), 'inference_release': threading.Event(),
             'loading_block': False, 'loading_entered': threading.Event(), 'loading_release': threading.Event(),
             'dispatch_pause': False, 'dispatch_paused': threading.Event()}
    class Catalog:
        def choose(self):
            return {'object_category': 'stub', 'episode_id': 'async-control-test'}
    class Simulator:
        def __init__(self, ep):
            self.steps=0; self.timestamp=0.; self.distance=0.; self.last_imu=None; self.last_render_ms=0.
        def metadata(self):
            return {'target':'stub', 'scene':'test-double'}
        def camera_pose(self):
            return np.zeros(3), np.array([0.,0.,0.,1.])
        def observe(self):
            return np.zeros((2,2,3),np.uint8),np.ones((2,2),np.float32),None
        def stationary_frame(self):
            self.timestamp+=1/30.
            self.last_imu={'gyro_body_rad_s':[0.,0.,0.],'specific_force_body_m_s2':[0.,9.80665,0.],'dt':1/30.}
            return self.observe()
        def advance(self, action):
            if self.steps>=500: raise AssertionError('501st motion attempted')
            stats['motions']+=1; self.steps+=1
            for index in range(8):
                self.distance+=.25/8
                yield self.stationary_frame()
        def close(self): pass
    class Localizer:
        def __init__(self):
            self.last=-1
            self.samples=0
            if stats['loading_block']:
                stats['loading_entered'].set()
                assert stats['loading_release'].wait(5)
        def reset(self,*a):pass
        def track(self,rgb,depth,timestamp,imu=None):
            assert timestamp>self.last
            self.last=timestamp
            samples=imu if isinstance(imu,list) else [imu] if imu is not None else []
            stats['imu_processed']+=len(samples)
            self.samples+=len(samples)
            return {'position':[0.,0.,0.], 'visual_tracking':'OK','map_points':[],
                    'imu_samples':self.samples}
        def close(self):pass
    class Policy:
        def reset(self,target):pass
        def act(self,*a,**kw):
            stats['policy_calls']+=1
            if stats['policy_calls']==stats['block_at']:
                stats['inference_entered'].set()
                assert stats['inference_release'].wait(5)
            time.sleep(.001)
            self.last_info={'raw_output':stats['mode'],'inference_ms':1.}
            return stats['mode']
    def module(name,**members):
        result=types.ModuleType(name);result.__dict__.update(members);return result
    replacements={'app.simulator':module('app.simulator',DroneSimulator=Simulator,EpisodeCatalog=Catalog),
                  'localization':module('localization',FusedLocalizer=Localizer),
                  'navigation':module('navigation',NavigationPolicy=Policy)}
    originals={k:sys.modules.get(k) for k in replacements}
    sys.modules.update(replacements)
    old_root=server.ROOT
    class TestPlatform(server.Platform):
        def _recognition(self):pass
        def _jpeg_worker(self):pass
        def _write_summary(self):pass
        def event(self,text):
            super().event(text)
            if stats['dispatch_pause'] and text.startswith('动作 '):
                stats['dispatch_pause']=False
                self.command('pause');stats['dispatch_paused'].set()
    report={};platform=None
    try:
        with tempfile.TemporaryDirectory(prefix='dronenav_async_test_') as temp:
            server.ROOT=Path(temp)
            platform=TestPlatform();platform.command('start')
            assert stats['inference_entered'].wait(5)
            platform.command('pause')
            frames=platform.frame_seq;motions=stats['motions'];steps=platform.state['steps']
            stats['inference_release'].set();time.sleep(.15)
            drained_frames=platform.frame_seq-frames
            # A rendered frame already in _new_frame may finish publication;
            # it must not start a new motion or consume another action.
            stable_seq=platform.frame_seq;time.sleep(.06)
            report['pause_in_flight']={'passed':stable_seq==platform.frame_seq and motions==stats['motions'] and
                                      steps==platform.state['steps'] and not platform.controller.metrics()['active'],
                                      'steps':steps,'frames':frames,'already_rendered_frames_drained':drained_frames,
                                      'late_motion_count':stats['motions']-motions}
            stats['mode']='STOP';platform.command('start')
            wait(lambda:platform.state.get('stop_reason')=='MODEL_STOP')
            report['resume_model_stop']={'passed':not platform.run_gate.is_set() and not platform.controller.metrics()['active'],
                                         'stop_reason':platform.state.get('stop_reason')}
            platform.command('reset');wait(lambda:not platform.state['has_started'])
            stats['mode']='FORWARD';before=stats['motions'];platform.command('start')
            wait(lambda:platform.state.get('stop_reason')=='STEP_LIMIT' or platform.state['status']=='error',30)
            time.sleep(.2)
            count=stats['motions']-before;call_count=stats['policy_calls']
            platform.command('start');time.sleep(.1)
            report['budget_500']={'passed':count==500 and platform.state['steps']==500 and
                                  not platform.run_gate.is_set() and not platform.controller.metrics()['active'] and
                                  stats['policy_calls']==call_count,
                                  'motion_calls':count,'steps':platform.state['steps'],'error':platform.state.get('error')}
            report['aggregated_imu']={'passed':platform.state.get('imu_samples')==platform.state.get('visual_imu_samples'),
                                      'sensor_samples':platform.state.get('imu_samples'),
                                      'visual_processed_samples':platform.state.get('visual_imu_samples')}
            platform.command('reset');wait(lambda:not platform.state['has_started'])
            stats['loading_block']=True;platform.command('start')
            assert stats['loading_entered'].wait(5)
            platform.command('pause');before=stats['policy_calls'];stats['loading_release'].set()
            wait(lambda:platform.state['has_started']);time.sleep(.15)
            report['pause_while_loading']={'passed':stats['policy_calls']==before and platform.state['steps']==0 and
                                          not platform.run_gate.is_set(),'calls_after_pause':stats['policy_calls']-before}
            platform.command('reset');wait(lambda:not platform.state['has_started'])
            stats['loading_block']=False;stats['dispatch_pause']=True;before=stats['motions']
            platform.command('start');assert stats['dispatch_paused'].wait(5);time.sleep(.1)
            report['pause_before_dispatch']={'passed':stats['motions']==before and platform.state['steps']==0 and
                                            not platform.controller.metrics()['active'],'motions_after_pause':stats['motions']-before}
            platform.command('exit');wait(lambda:not platform.state['has_started'])
            platform.exit_requested=True;platform.controller.shutdown.set()
    finally:
        if platform:
            platform.run_gate.clear();platform.exit_requested=True;platform.controller.shutdown.set()
            if platform._workers:platform._workers.close()
        server.ROOT=old_root
        for k,v in originals.items():
            if v is None:sys.modules.pop(k,None)
            else:sys.modules[k]=v
    report['test_scope']='real asynchronous Platform state machine; peripheral stubs and accelerated sensor clock'
    report['passed']=all(value['passed'] for value in report.values() if isinstance(value,dict))
    path=ROOT/'reports/realtime_runtime_regression.json';path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
