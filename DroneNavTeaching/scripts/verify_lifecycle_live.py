"""Actual model/GPU/platform startup -> simulation -> API exit acceptance.
Run only when intentionally ending the currently managed platform session.
"""
import json,subprocess,sys,time
from pathlib import Path
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts import manage

def get(path):
    with urlopen('http://127.0.0.1:%d%s'%(manage.SERVICES['platform']['port'],path),timeout=5) as response:return json.load(response)
def post(path):
    with urlopen(Request('http://127.0.0.1:%d%s'%(manage.SERVICES['platform']['port'],path),data=b'',method='POST'),timeout=5) as response:return response.status,json.load(response)
def gpu():
    run=subprocess.run(['nvidia-smi','--query-compute-apps=pid,used_memory','--format=csv,noheader,nounits'],capture_output=True,text=True)
    return [dict(pid=int(row.split(',')[0].strip()),used_memory_mib=int(row.split(',')[1].strip())) for row in run.stdout.splitlines() if row.strip() and row.split(',')[0].strip().isdigit()]
def main():
    before=manage._active_state();assert before and before['status']=='ready','Start with bash start.sh first'
    services={name:manage.status(name) for name in manage.SERVICES};assert all(manage._ready(v) for v in services.values())
    gpu_before=gpu();initial=get('/api/state');start_reply=post('/api/start')
    deadline=time.monotonic()+40;during=None;tracked={}
    while time.monotonic()<deadline:
        during=get('/api/state');value=manage._state()
        tracked={r['pid']:r for r in value.get('owned_processes',[])}
        orb=[r for r in tracked.values() if any('orb_ipc' in arg for arg in r['cmdline'])]
        if during.get('frame_seq',0)>=5 and orb:break
        if during.get('error'):raise AssertionError('Platform error '+str(during['error']))
        time.sleep(.2)
    assert during and during.get('frame_seq',0)>=5 and orb,'Live camera and ORB must actually run'
    pids=list(tracked)+[before['supervisor']['pid']]
    begin=time.perf_counter();code,reply=post('/api/exit');response_seconds=time.perf_counter()-begin
    assert code==202 and reply.get('status')=='exiting'
    deadline=time.monotonic()+25
    while time.monotonic()<deadline:
        if not manage._active_state():break
        time.sleep(.1)
    after=manage._state();remaining=[pid for pid in pids if manage.identity(pid)]
    marked=[r for r in manage._all_processes().values() if manage._has_marker(r['pid'],before['run_id'])]
    gpu_after=gpu();owned_gpu=[row for row in gpu_after if row['pid'] in pids]
    passed=after.get('status')=='stopped' and not manage._active_state() and not remaining and not marked and not owned_gpu
    report=dict(passed=passed,mode='Actual pretrained navigation and recognition processes + live Habitat + actual ORB worker',start_services=services,start_supervisor_pid=before['supervisor']['pid'],start_service_pids={k:v['pid'] for k,v in before['services'].items()},browser=before.get('browser'),gpu_before=gpu_before,initial_platform_status=initial.get('status'),start_reply=start_reply,simulation={k:during.get(k) for k in ['status','steps','frame_seq','run_id','position','error']},orb_pids=[r['pid'] for r in orb],exit_http_status=code,exit_reply=reply,exit_response_seconds=response_seconds,shutdown_seconds=time.perf_counter()-begin,checked_owned_pids=pids,remaining_owned_pids=remaining,remaining_marked_processes=marked,owned_gpu_processes_after=owned_gpu,gpu_after=gpu_after,final_status=after.get('status'))
    (ROOT/'reports/lifecycle_live.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));return 0 if passed else 1
if __name__=='__main__':raise SystemExit(main())
