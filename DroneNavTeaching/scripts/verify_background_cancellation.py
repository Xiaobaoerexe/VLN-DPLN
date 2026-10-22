"""Deterministic, model-free cancellation and old-episode callback regression."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import types

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
from app.server import EpisodeWorkers, Platform


class Controller:
    def __init__(self):self.pauses=0;self.recognitions=0
    def pause(self):self.pauses+=1
    def recognition_done(self,when):self.recognitions+=1


def platform_stub(directory):
    p=Platform.__new__(Platform)
    p.lock=threading.RLock();p.intent_generation=1;p.run_gate=threading.Event();p.run_gate.set()
    p.controller=Controller();p.exit_requested=False;p.frame_seq=1;p.frame_time=time.perf_counter()
    p.rgb=np.zeros((2,2,3),np.uint8)
    p.state={'run_id':'old','has_started':True,'steps':0,'status':'running','target':'old-target','detections':[]}
    p.run_dir=directory/'old';p.run_dir.mkdir()
    return p


def navigation_case(directory,cancel):
    p=platform_stub(directory);entered=threading.Event();release=threading.Event()
    class Policy:
        def act(self,*a,**kw):
            entered.set();assert release.wait(3);raise RuntimeError('delayed request failure')
    w=EpisodeWorkers.__new__(EpisodeWorkers)
    w.platform=p;w.policy=Policy();w.stop=threading.Event();w.condition=threading.Condition()
    w.result=None;w.navigation_seq=-1
    w.latest={'seq':1,'rgb':p.rgb,'depth':np.ones((2,2),np.float32),'pose':np.eye(4),
              'run_id':'old','generation':1}
    thread=threading.Thread(target=w._navigation);thread.start();assert entered.wait(3)
    if cancel:
        with p.lock:p.intent_generation=2;p.run_gate.clear();p.state['status']='paused'
    release.set();time.sleep(.08);w.stop.set()
    with w.condition:w.condition.notify_all()
    thread.join(2);assert not thread.is_alive()
    passed=(p.state['status']=='paused' and 'navigation_error' not in p.state and p.controller.pauses==0) if cancel else (
        p.state['status']=='error' and p.state.get('navigation_error')=='delayed request failure' and p.controller.pauses==1)
    return {'passed':passed,'status':p.state['status'],'controller_pause_calls':p.controller.pauses,
            'navigation_error':p.state.get('navigation_error')}


def recognition_case(directory,fail):
    p=platform_stub(directory);entered=threading.Event();release=threading.Event()
    class Recognition:
        def detect(self,*a):
            entered.set();assert release.wait(3)
            if fail:raise RuntimeError('old episode recognition failure')
            return {'boxes':[{'label':'old-target'}],'model':'stub'}
    original=sys.modules.get('recognition.client')
    replacement=types.ModuleType('recognition.client');replacement.RecognitionClient=Recognition
    sys.modules['recognition.client']=replacement
    thread=threading.Thread(target=p._recognition);thread.start();assert entered.wait(3)
    with p.lock:
        p.state.update(run_id='new',target='new-target',recognition_error=None)
        p.run_dir=directory/'new';p.run_dir.mkdir();p.rgb=None
    release.set();time.sleep(.08);p.exit_requested=True;thread.join(2)
    if original is None:sys.modules.pop('recognition.client',None)
    else:sys.modules['recognition.client']=original
    new_log=p.run_dir/'recognition.jsonl'
    passed=not p.state['detections'] and p.state.get('recognition_error') is None and not new_log.exists() and p.controller.recognitions==0
    return {'passed':passed,'new_run_detections':p.state['detections'],'new_run_error':p.state.get('recognition_error'),
            'new_run_log_exists':new_log.exists(),'controller_recognition_callbacks':p.controller.recognitions}


def main():
    report={}
    with tempfile.TemporaryDirectory(prefix='dronenav-background-') as tmp:
        root=Path(tmp)
        for name,kind in [('cancelled_navigation_error',True),('current_navigation_error',False)]:
            path=root/name;path.mkdir();report[name]=navigation_case(path,kind)
        for name,fail in [('old_recognition_success',False),('old_recognition_error',True)]:
            path=root/name;path.mkdir();report[name]=recognition_case(path,fail)
        p=platform_stub(root)
        p.state.update(run_id='new');p.run_dir=root/'new';p.run_dir.mkdir()
        p.log('recognition',{'target':'old-target'},run_id='old')
        report['log_guard']={'passed':not (p.run_dir/'recognition.jsonl').exists()}
    report['passed']=all(value['passed'] for value in report.values())
    output=ROOT/'reports/background_cancellation_regression.json';output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
