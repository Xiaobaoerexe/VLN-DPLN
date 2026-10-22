"""Open a real desktop browser on a static local page without loading models."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import manage

def main():
    run_id='browser-check-'+uuid.uuid4().hex
    title='DroneNav Browser Check '+run_id[-8:]
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(dict(path=self.path,user_agent=self.headers.get('User-Agent','')))
            body=('<!doctype html><title>'+title+'</title><h1>'+title+'</h1><p>Desktop browser launch succeeded. No models were loaded.</p>').encode()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_HEAD(self):
            self.send_response(200)
            self.send_header('Content-Type','text/html')
            self.end_headers()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url='http://127.0.0.1:%d/%s'%(server.server_address[1],run_id)
    before=manage._all_processes()
    result=manage.open_browser(url,run_id)
    deadline=time.monotonic()+30
    roots={}
    while time.monotonic()<deadline:
        current=manage._all_processes()
        for pid,record in current.items():
            if pid not in before and any(url in arg for arg in record['cmdline']):roots[pid]=record
        if any('Firefox' in row['user_agent'] for row in requests):break
        time.sleep(.2)
    windows=[]
    try:
        tree=subprocess.run(['xwininfo','-root','-tree'],env=manage._desktop_env(),capture_output=True,text=True,timeout=5)
        windows=[line.strip() for line in tree.stdout.splitlines() if title in line]
    except (OSError,subprocess.TimeoutExpired):pass
    owned=manage._discover(run_id,roots)
    launched=list(owned.values())
    # Only exact newly-created URL-bearing browser roots or marked dedicated
    # instances are closed; pre-existing user browsers are preserved.
    for record in owned.values():manage._signal(record,signal.SIGTERM)
    deadline=time.monotonic()+5
    while time.monotonic()<deadline and owned:
        time.sleep(.1)
        owned=manage._discover(run_id,owned)
    for record in owned.values():manage._signal(record,signal.SIGKILL)
    server.shutdown()
    server.server_close()
    remaining=[record for record in launched if manage.matches(record)]
    evidence=dict(passed=bool(any('Firefox' in row['user_agent'] for row in requests)),
        launch=result,requested_url=url,http_requests=requests,visible_window_evidence=windows,
        launched_processes=launched,remaining_test_owned_processes=remaining,
        model_services_started=False,desktop_environment={key:manage._desktop_env().get(key) for key in ['DISPLAY','XAUTHORITY','DBUS_SESSION_BUS_ADDRESS','XDG_DATA_DIRS']})
    (ROOT/'reports/browser_launch.json').write_text(json.dumps(evidence,indent=2))
    print(json.dumps(evidence,indent=2))
    return 0 if evidence['passed'] and not remaining else 1

if __name__=='__main__':raise SystemExit(main())
