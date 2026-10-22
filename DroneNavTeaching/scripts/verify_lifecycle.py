"""Isolated process lifecycle integration tests; do not touch live model services."""
import importlib.util,json,os,shutil,signal,socket,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parents[1]

def wait(test,timeout=20):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if test():return True
        time.sleep(.1)
    raise AssertionError('condition timed out')

def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def main():
    spec=importlib.util.spec_from_file_location('life_manage',ROOT/'scripts/manage.py');manage=importlib.util.module_from_spec(spec);spec.loader.exec_module(manage)
    evidence=[]
    with tempfile.TemporaryDirectory(prefix='dronenav-lifecycle-') as tmp:
        base=Path(tmp);(base/'scripts').mkdir();(base/'logs').mkdir()
        for source,dest in [(ROOT/'scripts/manage.py',base/'scripts/manage.py'),(ROOT/'supervisor.py',base/'supervisor.py'),(ROOT/'start.sh',base/'start.sh')]:shutil.copy2(source,dest)
        fixture=base/'fixture.py'
        fixture.write_text('''import json,os,subprocess,sys,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
role,port=sys.argv[1],int(sys.argv[2])
if role=='fail':raise SystemExit(17)
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
(Path(os.environ['DRONENAV_ROOT'])/'logs'/(role+'.child')).write_text(str(child.pid))
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  body=b'{"ready":true}';self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def do_POST(self):
  body=b'{"accepted":true,"status":"exiting"}';self.send_response(202);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);self.wfile.flush()
  from scripts.manage import request_shutdown
  threading.Timer(.2,lambda:request_shutdown(Path(os.environ['DRONENAV_ROOT']))).start()
 def log_message(self,*args):pass
ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
''')
        ports={name:free_port() for name in ['navigation','recognition','platform']}
        config=dict(services={name:dict(python=sys.executable,args=[str(fixture),name,str(port)],port=port) for name,port in ports.items()})
        cfg=base/'runtime.json';cfg.write_text(json.dumps(config))
        env=dict(os.environ,DRONENAV_ROOT=str(base));env.pop('DRONENAV_RUNTIME_CONFIG',None)
        def cli(*args):return subprocess.run([sys.executable,str(base/'scripts/manage.py')]+list(args),env=env,capture_output=True,text=True,timeout=30)
        def state():
            try:return json.loads((base/'logs/lifecycle.json').read_text())
            except (OSError,ValueError):return {}
        def stopped():
            value=state();return value.get('status')=='stopped' and not manage.matches(value.get('supervisor',{}))
        def all_recorded():
            value=state();records=value.get('owned_processes',[])+list(value.get('services',{}).values())+[value.get('supervisor',{})]
            pids=[r['pid'] for r in records if 'pid' in r]
            pids += [int(p.read_text()) for p in (base/'logs').glob('*.child')]
            return pids
        first=cli('start','--no-browser','--timeout','10');assert first.returncode==0,first.stderr+first.stdout+(base/'logs/supervisor.log').read_text()+''.join(p.read_text() for p in (base/'logs').glob('*.log'))
        value=state();pids=all_recorded();supervisor_pid=value['supervisor']['pid']
        again=cli('start','--no-browser','--timeout','10');assert again.returncode==0 and state()['supervisor']['pid']==supervisor_pid
        evidence.append(dict(test='start_and_duplicate_start',passed=True,owned_pids=pids))
        separate=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],cwd=base)
        forged=manage.identity(separate.pid);forged['starttime']+=1
        assert manage._signal(forged,signal.SIGTERM) is False and separate.poll() is None
        evidence.append(dict(test='reused_pid_starttime_mismatch_preserves_unrelated_process',passed=True,pid=separate.pid))
        t=time.monotonic();response=urlopen(Request('http://127.0.0.1:%d/api/exit'%ports['platform'],data=b'',method='POST'),timeout=3);body=json.load(response);elapsed=time.monotonic()-t
        assert body['status']=='exiting' and elapsed<.5
        wait(stopped);assert all(manage.identity(pid) is None for pid in pids),[pid for pid in pids if manage.identity(pid)]
        assert separate.poll() is None;separate.terminate();separate.wait()
        evidence.append(dict(test='response_precedes_exit_then_all_services_nested_children_supervisor_stop',passed=True,response_seconds=elapsed,remaining_project_pids=[]))
        bad=json.loads(json.dumps(config));bad['services']['recognition']['args']=[str(fixture),'fail',str(ports['recognition'])];cfg.write_text(json.dumps(bad))
        failed=cli('start','--no-browser','--timeout','10');assert failed.returncode!=0
        wait(stopped);assert not state()['remaining_processes']
        evidence.append(dict(test='startup_failure_cleans_already_started_services',passed=True,exit_code=failed.returncode))
        cfg.write_text(json.dumps(config))
        for signum in [signal.SIGTERM,signal.SIGINT,signal.SIGHUP]:
            with (base/'logs/foreground.log').open('ab') as log:
                launcher=subprocess.Popen(['bash',str(base/'start.sh'),'--no-browser','--timeout','10'],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
                wait(lambda:state().get('status')=='ready' and state()['supervisor']['pid']==launcher.pid)
                tracked=all_recorded();os.kill(launcher.pid,signum);launcher.wait(timeout=25)
            wait(stopped);assert all(manage.identity(pid) is None for pid in tracked)
            evidence.append(dict(test='foreground_start_sh_signal_'+str(signum)+'_cleans_children',passed=True,launcher_exit_code=launcher.returncode))
    result=dict(passed=all(row['passed'] for row in evidence),tests=evidence,scope='Isolated real Linux child processes and HTTP fixtures; does not replace actual GPU startup/exit acceptance.')
    (ROOT/'reports/lifecycle_integration.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
