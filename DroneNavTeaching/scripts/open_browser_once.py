"""A bounded, one-time login browser opener. It never starts model services."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

def _read(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return None

def _write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,indent=2));temp.replace(path)

def _quote(value):
    value=str(value)
    for char in ['\\','"','`','$']:value=value.replace(char,'\\'+char)
    return '"'+value.replace('%','%%')+'"'

def _remove_entry(path,root,token=None):
    try:text=path.read_text()
    except OSError:return
    if 'X-DroneNav-Root='+str(root)+'\n' not in text:return
    if token and 'X-DroneNav-Once-ID='+token+'\n' not in text:return
    path.unlink()

def register_deferred_browser(root,enabled=True,autostart_dir=None):
    """Called as the installing user, only for installation without a desktop.

    enabled=False cancels this project's pending login entry for --no-start.
    This function writes files only; it never runs a model or a browser.
    """
    root=Path(root).resolve()
    key=hashlib.sha256(str(root).encode()).hexdigest()[:16]
    home=Path(autostart_dir) if autostart_dir is not None else Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'autostart'
    entry=home/('dronenav-browser-once-'+key+'.desktop')
    pending=root/'state/browser-open-pending.json'
    old=_read(pending)
    if not enabled:
        _remove_entry(entry,root)
        if old and old.get('root')==str(root):pending.unlink(missing_ok=True)
        return dict(registered=False,reason='no_start',autostart_path=str(entry))
    if entry.exists() and 'X-DroneNav-Root='+str(root)+'\n' not in entry.read_text():
        raise RuntimeError('Preserving an unrelated autostart entry: '+str(entry))
    token=uuid.uuid4().hex
    value=dict(root=str(root),token=token,created_at_unix=time.time(),autostart_path=str(entry),timeout_seconds=180)
    _write(pending,value)
    command=' '.join([_quote('/usr/bin/python3'),_quote(root/'scripts/open_browser_once.py'),'--open','--root',_quote(root),'--token',_quote(token),'--timeout','180'])
    entry.parent.mkdir(parents=True,exist_ok=True)
    entry.write_text('[Desktop Entry]\nType=Application\nName=Open the installed DroneNav demo once\nExec='+command+'\nTerminal=false\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\nX-DroneNav-Root='+str(root)+'\nX-DroneNav-Once-ID='+token+'\n')
    return dict(registered=True,autostart_path=str(entry),pending_path=str(pending),token=token)

def run_once(root,token,timeout=180,manager=None,poll_interval=.25,startup_grace=15):
    root=Path(root).resolve();pending=root/'state/browser-open-pending.json'
    value=_read(pending)
    if not value or value.get('root')!=str(root) or value.get('token')!=token:
        return dict(status='not_pending',opened=False,started_models=False)
    if manager is None:
        os.environ['DRONENAV_ROOT']=str(root);sys.path.insert(0,str(root))
        from scripts import manage as manager
    with (root/'state/browser-open-once.lock').open('a') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return dict(status='another_opener_running',opened=False,started_models=False)
        value=_read(pending)
        if not value or value.get('token')!=token:
            return dict(status='not_pending',opened=False,started_models=False)
        # Remove before waiting: later logins cannot recreate a persistent waiter.
        _remove_entry(Path(value['autostart_path']),root,token)
        start=time.monotonic();deadline=start+min(float(timeout),180.)
        seen_active=False;result=dict(status='timeout',opened=False,started_models=False)
        while time.monotonic()<deadline:
            current_pending=_read(pending)
            if not current_pending or current_pending.get('token')!=token:
                result['status']='cancelled';break
            active=manager._active_state();last=manager._state()
            if active:
                seen_active=True
                if active.get('status')=='ready' and manager._ready(manager.status('platform')):
                    url='http://127.0.0.1:%d/'%manager.SERVICES['platform']['port']
                    browser=manager.open_browser(url,active['run_id'])
                    opened=browser.get('returncode')==0 or bool(browser.get('pid'))
                    result.update(status='opened' if opened else 'browser_unavailable',opened=opened,url=url,run_id=active['run_id'],browser=browser)
                    break
            else:
                stopped_after_registration=bool(last and last.get('status') in ['stopped','cleanup_failed'] and last.get('stopped_at_unix',0)>=value['created_at_unix'])
                if seen_active or stopped_after_registration or time.monotonic()-start>=startup_grace:
                    result['status']='platform_not_running';break
            time.sleep(poll_interval)
        result.update(elapsed_seconds=time.monotonic()-start,token=token,waiting_limit_seconds=min(float(timeout),180.))
        current=_read(pending)
        if current and current.get('token')==token:pending.unlink(missing_ok=True)
        _write(root/'logs/browser_login_once.json',result)
        return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True);parser.add_argument('--register',action='store_true')
    parser.add_argument('--no-start',action='store_true');parser.add_argument('--open',action='store_true')
    parser.add_argument('--token');parser.add_argument('--timeout',type=float,default=180)
    args=parser.parse_args()
    if args.register:result=register_deferred_browser(args.root,enabled=not args.no_start)
    elif args.open and args.token:result=run_once(args.root,args.token,args.timeout)
    else:parser.error('Use --register or --open --token TOKEN')
    print(json.dumps(result,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
