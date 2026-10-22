#!/usr/bin/env python3
"""Offline platform supervisor with precise Linux /proc ownership checks.

One foreground supervisor owns all three services and every marked descendant.
The HTTP exit hook calls request_shutdown() after its response has been flushed.
No process-name kill, broad environment kill, or system-wide browser termination.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
from urllib.request import urlopen

SOURCE_ROOT=Path(__file__).resolve().parents[1]
ROOT=Path(os.environ.get('DRONENAV_ROOT',str(SOURCE_ROOT))).expanduser().resolve()
LOGS=ROOT/'logs'
STATE=LOGS/'lifecycle.json'
MARKER='DRONENAV_RUN_ID'

def _config():
    p=Path(os.environ.get('DRONENAV_RUNTIME_CONFIG',str(ROOT/'runtime.json')))
    return json.loads(p.read_text()) if p.is_file() else {}

def _absolute(value):
    p=Path(value).expanduser()
    return str(p if p.is_absolute() else ROOT/p)

def _python(role,env_name):
    explicit=os.environ.get('DRONENAV_'+env_name.upper()+'_PYTHON')
    if explicit:return _absolute(explicit)
    explicit=_config().get('python',{}).get(role)
    if explicit:return _absolute(explicit)
    prefix=os.environ.get('CONDA_PREFIX','')
    choices=[ROOT/'runtime'/env_name/'bin/python',ROOT/'runtime/envs'/env_name/'bin/python']
    if role=='recognition':choices.append(ROOT/'recognition/.venv/bin/python')
    if prefix:choices += [Path(prefix).parent/env_name/'bin/python',Path(prefix).parent/'envs'/env_name/'bin/python']
    # Compatibility only: a delivered offline bundle takes precedence.
    choices += [Path.home()/'anaconda3/envs'/env_name/'bin/python',Path.home()/'miniconda3/envs'/env_name/'bin/python']
    return str(next((p for p in choices if p.is_file()),choices[0]))

def _services():
    cfg=_config();ports=cfg.get('ports',{})
    services={
      'navigation':dict(port=int(os.environ.get('DRONENAV_POLICY_PORT',ports.get('navigation',5811))),health='/health',python=_python('navigation','internnav'),args=['-m','navigation.server']),
      'recognition':dict(port=int(os.environ.get('DRONENAV_RECOGNITION_PORT',ports.get('recognition',8766))),health='/health',python=_python('recognition','recognition'),args=[str(ROOT/'recognition/service.py')]),
      'platform':dict(port=int(os.environ.get('DRONENAV_UI_PORT',ports.get('platform',5800))),health='/api/health',python=_python('platform','ovon'),args=['-m','app.server'])}
    services['recognition']['args']+=['--port',str(services['recognition']['port'])]
    services['platform']['args']+=['--port',str(services['platform']['port'])]
    for name,override in cfg.get('services',{}).items():
        if name not in services:raise ValueError('Unknown service '+name)
        services[name].update(override)
        if 'python' in override:services[name]['python']=_absolute(override['python'])
    return services

SERVICES=_services()

def identity(pid,role=None):
    """Immutable PID identity: Linux start ticks, full argv, cwd and UID."""
    try:
        pid=int(pid);base=Path('/proc')/str(pid)
        tail=(base/'stat').read_text().rsplit(')',1)[1].split()
        if tail[0]=='Z':return None
        args=[v.decode('utf-8','replace') for v in (base/'cmdline').read_bytes().split(b'\0') if v]
        if not args:return None
        info=dict(pid=pid,starttime=int(tail[19]),cmdline=args,cwd=os.readlink(str(base/'cwd')),uid=base.stat().st_uid,ppid=int(tail[1]))
        if role:info['role']=role
        return info
    except (OSError,ValueError,IndexError):return None

def matches(record):
    now=identity(record.get('pid',-1))
    return bool(now and all(now[k]==record[k] for k in ['starttime','cmdline','cwd','uid']))

def _state():
    try:
        value=json.loads(STATE.read_text())
        return value if value.get('root')==str(ROOT) else None
    except (OSError,ValueError):return None

def _active_state():
    value=_state()
    return value if value and value.get('supervisor') and matches(value['supervisor']) else None

def _write_state(value):
    LOGS.mkdir(exist_ok=True,parents=True)
    temp=STATE.with_suffix('.json.tmp')
    temp.write_text(json.dumps(value,indent=2));temp.chmod(0o600);temp.replace(STATE)

def status(name):
    info=SERVICES[name]
    try:
        with urlopen('http://127.0.0.1:%d%s'%(info['port'],info['health']),timeout=2) as response:return json.load(response)
    except Exception:return None

def _ready(value):return bool(value and (value.get('ready') is True or value.get('status')=='ready'))

def request_shutdown(root=None):
    """Non-blocking exit hook: send one authenticated request; never spawn."""
    base=Path(root).resolve() if root else ROOT
    try:
        value=json.loads((base/'logs/lifecycle.json').read_text())
        if value.get('root')!=str(base) or not matches(value['supervisor']):return False
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
            s.settimeout(2);s.connect(value['socket'])
            s.sendall((json.dumps({'action':'shutdown','token':value['token']})+'\n').encode())
            response=s.recv(4096)
        return bool(json.loads(response).get('accepted'))
    except (OSError,ValueError,KeyError):return False

def _has_marker(pid,run_id):
    try:
        env=(Path('/proc')/str(pid)/'environ').read_bytes().split(b'\0')
        return (MARKER+'='+run_id).encode() in env and ('DRONENAV_ROOT='+str(ROOT)).encode() in env
    except OSError:return False

def _all_processes():
    found={}
    for path in Path('/proc').iterdir():
        if path.name.isdigit():
            item=identity(path.name)
            if item and item['uid']==os.getuid():found[item['pid']]=item
    return found

def _discover(run_id,known):
    found=_all_processes()
    owned={p:r for p,r in known.items() if matches(r)}
    for pid,record in found.items():
        if pid!=os.getpid() and _has_marker(pid,run_id):owned[pid]=record
    changed=True
    while changed:
        changed=False
        for pid,record in found.items():
            if pid!=os.getpid() and pid not in owned and record['ppid'] in owned:
                owned[pid]=record;changed=True
    return owned

def _signal(record,signum):
    if not matches(record):return False
    try:os.kill(record['pid'],signum);return True
    except ProcessLookupError:return False

def _legacy_record(name):
    """Adopt only an exact project service listed by the previous project launcher."""
    try:record=identity(int((LOGS/(name+'.pid')).read_text()),name)
    except (OSError,ValueError):return None
    if not record or record['uid']!=os.getuid():return None
    try:Path(record['cwd']).resolve().relative_to(ROOT)
    except ValueError:return None
    argv=record['cmdline'];joined=' '.join(argv)
    expected={'navigation': ['navigation.server',str(ROOT/'navigation/server.py')],
              'recognition':[str(ROOT/'recognition/service.py')],
              'platform':['app.server',str(ROOT/'app/server.py')]}[name]
    return record if any(part in argv or part in joined for part in expected) else None

def _desktop_env():
    env=os.environ.copy()
    if not env.get('DISPLAY') and not env.get('WAYLAND_DISPLAY'):
        # Read the actual desktop user's session environment; never guess DISPLAY.
        for record in _all_processes().values():
            executable=Path(record['cmdline'][0]).name
            if executable not in ('gnome-shell','plasmashell','xfce4-session','gnome-session-binary'):continue
            try:
                pairs=(Path('/proc')/str(record['pid'])/'environ').read_bytes().split(b'\0')
                actual=dict(p.decode('utf-8','replace').split('=',1) for p in pairs if b'=' in p)
                for key in ['DISPLAY','WAYLAND_DISPLAY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','XAUTHORITY','XDG_CURRENT_DESKTOP','XDG_DATA_DIRS','XDG_CONFIG_DIRS','PATH','XDG_SESSION_TYPE']:
                    if actual.get(key):env[key]=actual[key]
                if env.get('DISPLAY') or env.get('WAYLAND_DISPLAY'):break
            except OSError:pass
    return env

def _open_browser_unlocked(url,run_id=None):
    env=_desktop_env()
    if not env.get('DISPLAY') and not env.get('WAYLAND_DISPLAY'):
        print('No desktop session found; open this URL from your browser: '+url,flush=True)
        return dict(attempted=False,reason='no_desktop_session',url=url)
    configured=os.environ.get('DRONENAV_BROWSER') or _config().get('browser',{}).get('executable')
    bundled=ROOT/'browser/firefox/firefox'
    attempts=[]
    if configured:
        resolved=_absolute(configured)
        is_bundled=Path(resolved).resolve()==bundled.resolve()
        candidates=[('bundled_firefox' if is_bundled else 'configured',resolved)]
    else:
        candidates=[]
        system=shutil.which('xdg-open')
        if system:candidates.append(('default',system))
        if bundled.is_file():candidates.append(('bundled_firefox',str(bundled)))
    for kind,command in candidates:
        browser_env=env.copy();browser_env.pop(MARKER,None);browser_env.pop('DRONENAV_ROOT',None)
        if kind=='bundled_firefox':
            # The offline browser uses a dedicated profile, so only this owned
            # instance and its marked descendants can be closed with the demo.
            profile=LOGS/'browser-profile';profile.mkdir(exist_ok=True)
            args=[command,'--no-remote','--profile',str(profile),'--new-window',url]
            if run_id:browser_env.update(DRONENAV_ROOT=str(ROOT),DRONENAV_RUN_ID=run_id)
        else:args=[command,url]
        try:
            with (LOGS/'browser.log').open('ab') as log:
                proc=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=browser_env,start_new_session=True)
                if kind=='bundled_firefox':
                    time.sleep(.5);code=proc.poll()
                    if code is None:
                        print('Offline browser opened: '+url,flush=True)
                        return dict(attempted=True,kind=kind,pid=proc.pid,url=url,display=env.get('DISPLAY'),attempts=attempts)
                else:
                    try:code=proc.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        record=identity(proc.pid)
                        if record:_signal(record,signal.SIGTERM)
                        try:proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            if record:_signal(record,signal.SIGKILL)
                            proc.wait()
                        code=proc.returncode
            attempts.append(dict(kind=kind,command=command,returncode=code))
            if code==0:
                print('Default browser request sent: '+url,flush=True)
                return dict(attempted=True,kind=kind,returncode=code,url=url,display=env.get('DISPLAY'),attempts=attempts)
        except OSError as error:attempts.append(dict(kind=kind,command=command,error=str(error)))
    print('No browser opened automatically; open '+url,flush=True)
    return dict(attempted=bool(candidates),reason='browser_launch_failed',url=url,attempts=attempts)

def open_browser(url,run_id=None):
    """One successful browser request per run, shared with the login opener."""
    if not run_id:return _open_browser_unlocked(url,run_id)
    LOGS.mkdir(exist_ok=True,parents=True)
    key=hashlib.sha256(str(run_id).encode()).hexdigest()[:20]
    receipt=LOGS/('browser-open-'+key+'.json')
    with (LOGS/('browser-open-'+key+'.lock')).open('a') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        try:
            previous=json.loads(receipt.read_text())
            if previous.get('success') and previous.get('url')==url:
                result=dict(previous['result']);result['deduplicated']=True;return result
        except (OSError,ValueError,KeyError):pass
        result=_open_browser_unlocked(url,run_id)
        success=result.get('returncode')==0 or bool(result.get('pid'))
        value=dict(run_id=run_id,url=url,success=success,result=result,requested_at_unix=time.time())
        temporary=receipt.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(value,indent=2));temporary.chmod(0o600);temporary.replace(receipt)
        return result

class Supervisor:
    def __init__(self,open_default=False,timeout=180):
        self.run_id=uuid.uuid4().hex;self.known={};self.children={};self.stop_event=threading.Event();self.reason='requested';self.timeout=timeout;self.open_default=open_default
        self.socket_path=str(Path('/tmp')/('dronenav-%d-%s.sock'%(os.getuid(),hashlib.sha256(str(ROOT).encode()).hexdigest()[:16])))
        self.token=uuid.uuid4().hex;self.control=None;self.lock=None
        self.value=dict(version=2,root=str(ROOT),run_id=self.run_id,socket=self.socket_path,token=self.token,status='starting',supervisor=identity(os.getpid(),'supervisor'),services={},owned_processes=[],browser=None)

    def save(self):
        self.value['owned_processes']=list(self.known.values());self.value['updated_at_unix']=time.time();_write_state(self.value)

    def request(self,reason):self.reason=reason;self.stop_event.set()

    def _control_loop(self):
        while not self.stop_event.is_set():
            try:
                ready,_,_=select.select([self.control],[],[],.2)
                if not ready:continue
                conn,_=self.control.accept()
                with conn:
                    conn.settimeout(2);raw=conn.recv(4096);request=json.loads(raw)
                    accepted=request.get('token')==self.token and request.get('action')=='shutdown'
                    conn.sendall(json.dumps({'accepted':accepted}).encode())
                    if accepted:self.request('frontend_or_cli_exit')
            except (OSError,ValueError):continue

    def _spawn(self,name):
        health=status(name)
        legacy=_legacy_record(name)
        if health:
            if legacy:
                self.known[legacy['pid']]=legacy;self.value['services'][name]=legacy
                print('%s: adopted exact project pid=%s'%(name,legacy['pid']),flush=True);return
            raise RuntimeError('%s port %s already answers but has no verifiable project PID; preserved'%(name,SERVICES[name]['port']))
        if legacy:
            raise RuntimeError('%s has an alive legacy project PID but no health response; stop it explicitly before retrying'%name)
        info=SERVICES[name]
        if not Path(info['python']).is_file():raise RuntimeError('Runtime missing for %s: %s; run the offline installer or configure runtime.json'%(name,info['python']))
        cache=ROOT/'state'/'cache'
        for path in [cache,cache/'huggingface',cache/'torch',cache/'triton',cache/'cuda']:
            path.mkdir(parents=True,exist_ok=True)
        env=dict(os.environ,PYTHONPATH=str(ROOT),DRONENAV_ROOT=str(ROOT),DRONENAV_RUN_ID=self.run_id,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_DATASETS_OFFLINE='1',YOLO_OFFLINE='true',YOLO_AUTOINSTALL='false',WANDB_MODE='disabled',PYTHONNOUSERSITE='1',PYTHONUNBUFFERED='1',HABITAT_SIM_LOG='quiet',MAGNUM_LOG='quiet',XDG_CACHE_HOME=str(cache),HF_HOME=str(cache/'huggingface'),TORCH_HOME=str(cache/'torch'),TRITON_CACHE_DIR=str(cache/'triton'),CUDA_CACHE_PATH=str(cache/'cuda'))
        env['DRONENAV_POLICY_PORT']=str(SERVICES['navigation']['port']);env['DRONENAV_RECOGNITION_PORT']=str(SERVICES['recognition']['port'])
        with (LOGS/(name+'.log')).open('ab') as log:
            process=subprocess.Popen([info['python']]+info['args'],cwd=str(ROOT),env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        self.children[name]=process
        record=None
        for _ in range(40):
            record=identity(process.pid,name)
            if record or process.poll() is not None:break
            time.sleep(.005)
        if not record:raise RuntimeError(name+' process exited before registration, code='+str(process.poll()))
        self.known[process.pid]=record;self.value['services'][name]=record
        (LOGS/(name+'.pid')).write_text(str(process.pid))
        print('%s: launched pid=%d'%(name,process.pid),flush=True);self.save()

    def _refresh(self):self.known=_discover(self.run_id,self.known)

    def _service_failure(self):
        for name,record in list(self.value['services'].items()):
            current=identity(record['pid'],name)
            if not current or any(current[k]!=record[k] for k in ['starttime','cmdline','uid']):return name+' exited'
            # Navigation intentionally chdirs into its vendored repository while importing.
            # Refresh cwd only inside this project; each actual signal still verifies exact cwd.
            try:Path(current['cwd']).resolve().relative_to(ROOT)
            except ValueError:return name+' moved outside the project'
            self.value['services'][name]=current;self.known[current['pid']]=current
        return None

    def cleanup(self):
        self.value['status']='stopping';self.value['reason']=self.reason;self._refresh();self.save()
        signaled=set();deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            self._refresh()
            if not self.known:break
            for pid,record in self.known.items():
                key=(pid,record['starttime'])
                if key not in signaled:_signal(record,signal.SIGTERM);signaled.add(key)
            for child in self.children.values():child.poll()
            time.sleep(.1)
        kill_deadline=time.monotonic()+5
        while time.monotonic()<kill_deadline:
            self._refresh()
            if not self.known:break
            for record in self.known.values():_signal(record,signal.SIGKILL)
            for child in self.children.values():child.poll()
            time.sleep(.1)
        for child in self.children.values():
            try:child.wait(timeout=1)
            except subprocess.TimeoutExpired:pass
        self._refresh();self.value['status']='stopped' if not self.known else 'cleanup_failed'
        self.value['remaining_processes']=list(self.known.values());self.value['stopped_at_unix']=time.time();self.save()
        for name,record in self.value['services'].items():
            pidfile=LOGS/(name+'.pid')
            try:
                if pidfile.read_text().strip()==str(record['pid']):pidfile.unlink()
            except OSError:pass
        if self.control:self.control.close()
        try:Path(self.socket_path).unlink()
        except FileNotFoundError:pass
        print('DRONENAV_STOPPED remaining_project_processes=%d'%len(self.known),flush=True)

    def run(self):
        LOGS.mkdir(parents=True,exist_ok=True)
        self.lock=(LOGS/'lifecycle.lock').open('a')
        try:fcntl.flock(self.lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('Platform supervisor already running: http://127.0.0.1:%d/'%SERVICES['platform']['port'],flush=True);return 0
        for signum in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(signum,lambda s,f:self.request('signal_'+str(s)))
        exit_code=0
        try:
            path=Path(self.socket_path)
            if path.exists():
                if path.stat().st_uid!=os.getuid() or not stat.S_ISSOCK(path.stat().st_mode):raise RuntimeError('Unsafe existing control socket')
                path.unlink()
            self.control=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.control.bind(self.socket_path);path.chmod(0o600);self.control.listen(4)
            self.save();threading.Thread(target=self._control_loop,daemon=True).start()
            for name in SERVICES:
                if self.stop_event.is_set():return 0
                self._spawn(name)
            deadline=time.monotonic()+self.timeout
            while not self.stop_event.is_set():
                self._refresh();failure=self._service_failure()
                if failure:raise RuntimeError(failure)
                if all(_ready(status(name)) for name in SERVICES):break
                if time.monotonic()>deadline:raise RuntimeError('Service startup timed out')
                self.save();self.stop_event.wait(.25)
            if not self.stop_event.is_set():
                self.value['status']='ready';self.save()
                url='http://127.0.0.1:%d/'%SERVICES['platform']['port'];print('DRONENAV_READY '+url,flush=True)
                if self.open_default:self.value['browser']=open_browser(url,self.run_id);self.save()
            while not self.stop_event.wait(.25):
                self._refresh();failure=self._service_failure()
                if failure:raise RuntimeError(failure)
                self.save()
        except BaseException as exc:
            self.reason='failure: '+repr(exc);print(self.reason,flush=True);exit_code=1
        finally:
            self.stop_event.set();self.cleanup();self.lock.close()
        return exit_code

def stop_all(timeout=25):
    value=_active_state()
    if not value:
        # Stale state cannot authorize signaling a reused PID.
        print('No live managed platform supervisor; unrelated processes preserved',flush=True);return 0
    if not request_shutdown():raise RuntimeError('Managed supervisor is alive but control request failed; inspect logs')
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        current=_state()
        if not matches(value['supervisor']):
            remaining=[r for r in (current or {}).get('remaining_processes',[]) if matches(r)]
            if remaining:raise RuntimeError('Managed descendants remain: '+str([r['pid'] for r in remaining]))
            print('All managed platform services and descendants stopped',flush=True);return 0
        time.sleep(.1)
    raise RuntimeError('Platform shutdown timed out')

def start(name='all',open_default=False,timeout=180):
    # Preserve the public helper used by read-only management checks.
    if name!='all' and status(name):
        print(name+(': starting/not ready; existing service preserved' if not _ready(status(name)) else ': already running (ready)'));return 0
    active=_active_state()
    if active:
        print('Platform supervisor already running; existing services preserved',flush=True);return 0
    LOGS.mkdir(exist_ok=True,parents=True)
    command=[sys.executable,str(SOURCE_ROOT/'supervisor.py'),'--timeout',str(timeout)]
    if open_default:command.append('--open-browser')
    with (LOGS/'supervisor.log').open('ab') as log:
        child=subprocess.Popen(command,cwd=str(ROOT),env=dict(os.environ,DRONENAV_ROOT=str(ROOT)),stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    deadline=time.monotonic()+timeout+5
    while time.monotonic()<deadline:
        value=_active_state()
        if value and value['status']=='ready':print('DRONENAV_READY http://127.0.0.1:%d/'%SERVICES['platform']['port'],flush=True);return 0
        code=child.poll()
        if code is not None:raise RuntimeError('Supervisor exited code=%s; inspect logs/supervisor.log'%code)
        time.sleep(.2)
    request_shutdown();raise RuntimeError('Startup did not become ready in time')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['start','run','stop','restart','status'])
    parser.add_argument('service',nargs='?',choices=['all']+list(SERVICES),default='all')
    parser.add_argument('--open-browser',action='store_true');parser.add_argument('--no-browser',action='store_true');parser.add_argument('--timeout',type=float,default=180)
    args=parser.parse_args()
    if args.action=='status':
        value=_state();print(json.dumps(dict(supervisor_active=bool(_active_state()),lifecycle=value,services={name:status(name) for name in SERVICES}),indent=2));return 0
    if args.action in ['stop','restart']:stop_all()
    if args.action=='run':return Supervisor(open_default=args.open_browser and not args.no_browser,timeout=args.timeout).run()
    if args.action in ['start','restart']:return start(args.service,args.open_browser and not args.no_browser,args.timeout)
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:print(str(error),file=sys.stderr,flush=True);raise SystemExit(1)
