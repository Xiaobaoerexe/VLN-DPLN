"""Static regression for the bounded login hook and per-run browser lock."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.open_browser_once import register_deferred_browser,run_once
from scripts import manage

class Fixture:
    SERVICES={'platform':{'port':5800}}
    def __init__(self,states,last=None):self.states=list(states);self.last=last;self.calls=[]
    def _active_state(self):
        if len(self.states)>1:return self.states.pop(0)
        return self.states[0]
    def _state(self):return self.last
    def _ready(self,value):return value.get('ready') is True
    def status(self,name):return {'ready':True}
    def open_browser(self,url,run_id):self.calls.append((url,run_id));return {'kind':'default','returncode':0}

def main():
    tests=[]
    with tempfile.TemporaryDirectory(prefix='dronenav-login-test-') as temporary:
        top=Path(temporary)
        def register(name):
            root=top/name;root.mkdir()
            return root,register_deferred_browser(root,autostart_dir=root/'fake-home/autostart')
        root,entry=register('ready')
        content=Path(entry['autostart_path']).read_text()
        assert 'open_browser_once.py' in content and 'start.sh' not in content and 'manage.py start' not in content
        fixture=Fixture([{'status':'ready','run_id':'fixture-ready'}])
        result=run_once(root,entry['token'],manager=fixture,poll_interval=.001)
        repeat=run_once(root,entry['token'],manager=fixture,poll_interval=.001)
        assert result['opened'] and repeat['status']=='not_pending' and len(fixture.calls)==1
        assert not Path(entry['autostart_path']).exists()
        tests.append(dict(test='login_opens_existing_ready_platform_once_and_removes_autostart',passed=True))
        root,entry=register('no-start')
        cancelled=register_deferred_browser(root,enabled=False,autostart_dir=root/'fake-home/autostart')
        assert not cancelled['registered'] and not Path(entry['autostart_path']).exists()
        fixture=Fixture([{'status':'ready','run_id':'must-not-open'}])
        assert run_once(root,entry['token'],manager=fixture)['status']=='not_pending' and not fixture.calls
        tests.append(dict(test='no_start_cancels_pending_and_never_opens_or_starts_models',passed=True))
        root,entry=register('stopped')
        fixture=Fixture([None],last={'status':'stopped','stopped_at_unix':time.time()+1})
        result=run_once(root,entry['token'],manager=fixture,poll_interval=.001)
        assert result['status']=='platform_not_running' and result['elapsed_seconds']<.1 and not fixture.calls
        tests.append(dict(test='exit_before_login_stops_immediately_without_restarting',passed=True))
        root,entry=register('dies')
        fixture=Fixture([{'status':'starting','run_id':'dies'},None])
        result=run_once(root,entry['token'],manager=fixture,poll_interval=.001)
        assert result['status']=='platform_not_running' and not fixture.calls
        tests.append(dict(test='platform_exits_while_waiting_login_hook_cancels',passed=True))
        root,entry=register('timeout')
        fixture=Fixture([{'status':'starting','run_id':'never-ready'}])
        result=run_once(root,entry['token'],timeout=.025,manager=fixture,poll_interval=.001)
        assert result['status']=='timeout' and result['elapsed_seconds']<.1 and not fixture.calls
        assert not Path(entry['autostart_path']).exists()
        tests.append(dict(test='not_ready_wait_is_bounded_and_has_no_future_login_entry',passed=True))
        old_logs,original=manage.LOGS,manage._open_browser_unlocked
        try:
            manage.LOGS=top/'locks';calls=[]
            def fake_browser(url,run_id):
                calls.append((url,run_id));time.sleep(.02);return {'kind':'default','returncode':0}
            manage._open_browser_unlocked=fake_browser
            results=[]
            threads=[threading.Thread(target=lambda:results.append(manage.open_browser('http://127.0.0.1:5800/','concurrent-run'))) for _ in range(2)]
            for thread in threads:thread.start()
            for thread in threads:thread.join()
            assert len(calls)==1 and sum(bool(r.get('deduplicated')) for r in results)==1
            tests.append(dict(test='supervisor_and_login_hook_concurrently_request_one_browser',passed=True))
            calls.clear()
            def no_desktop_then_ready(url,run_id):
                calls.append((url,run_id))
                return {'attempted':False,'reason':'no_desktop_session'} if len(calls)==1 else {'kind':'default','returncode':0}
            manage._open_browser_unlocked=no_desktop_then_ready
            first=manage.open_browser('http://127.0.0.1:5800/','desktop-later')
            second=manage.open_browser('http://127.0.0.1:5800/','desktop-later')
            third=manage.open_browser('http://127.0.0.1:5800/','desktop-later')
            assert first['reason']=='no_desktop_session' and second['returncode']==0 and third['deduplicated'] and len(calls)==2
            tests.append(dict(test='headless_boot_request_does_not_consume_later_login_open',passed=True))
        finally:manage.LOGS,manage._open_browser_unlocked=old_logs,original
    report=dict(passed=True,tests=tests,model_services_started=False,scope='Static login-state and locking regressions. Real default and bundled browser windows are evidenced separately.')
    (ROOT/'reports/login_browser_once.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
