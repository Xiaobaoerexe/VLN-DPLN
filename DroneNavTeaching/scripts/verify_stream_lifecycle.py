"""Pure HTTP fixtures: run-bound MJPEG closes on reset/new run/exit."""
import http.client
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.server import Handler


def main():
    lock=threading.RLock()
    p=SimpleNamespace(lock=lock,stream_condition=threading.Condition(lock),exit_requested=False,
                      state={'has_started':True,'run_id':'one','jpeg_seq':7,'status':'running'},
                      frame=b'fixture-jpeg',overview=b'fixture-overview')
    class FixtureHandler(Handler):platform=p
    service=ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
    thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
    connections=[];report={}
    def connect(query=''):
        c=http.client.HTTPConnection('127.0.0.1',service.server_port,timeout=3)
        connections.append(c);c.request('GET','/api/stream.mjpg?view=front'+query)
        return c,c.getresponse()
    def first_frame(response):
        assert response.status==200
        assert response.readline()==b'--frame\r\n'
        size=None
        while True:
            line=response.readline()
            if line==b'\r\n':break
            if line.startswith(b'Content-Length:'):size=int(line.split(b':',1)[1])
        assert size is not None
        body=response.read(size);assert response.read(2)==b'\r\n'
        return body
    try:
        _,response=connect('&run=one');assert first_frame(response)==p.frame
        start=time.perf_counter()
        with p.stream_condition:
            p.state.update(has_started=False,status='idle');p.frame=p.overview=None
            p.stream_condition.notify_all()
        assert response.read()==b''
        report['reset_closes_old_stream']={'passed':time.perf_counter()-start<.25,
                                           'eof_ms':(time.perf_counter()-start)*1000}
        # Old non-negative jpeg_seq with no body used to make the predicate spin.
        wall=time.perf_counter();cpu=time.process_time()
        for i in range(10):
            c,r=connect('&run=one');assert r.status==204 and r.read()==b'';c.close()
        cpu_used=time.process_time()-cpu
        report['idle_stale_sequence_no_spin']={'passed':cpu_used<.3,'requests':10,'cpu_seconds':cpu_used,
                                               'wall_seconds':time.perf_counter()-wall}
        with p.stream_condition:
            p.state.update(has_started=True,run_id='two',jpeg_seq=8,status='running');p.frame=b'new-jpeg'
            p.stream_condition.notify_all()
        _,response=connect()  # No run argument binds to the run at connection time.
        assert first_frame(response)==b'new-jpeg'
        with p.stream_condition:
            p.state['run_id']='three';p.stream_condition.notify_all()
        assert response.read()==b''
        report['new_run_and_implicit_binding']={'passed':True}
        _,response=connect('&run=three');assert first_frame(response)==b'new-jpeg'
        with p.stream_condition:p.exit_requested=True;p.stream_condition.notify_all()
        assert response.read()==b''
        report['exit_closes_stream']={'passed':True}
    finally:
        with p.stream_condition:p.exit_requested=True;p.stream_condition.notify_all()
        for connection in connections:connection.close()
        service.shutdown();service.server_close();thread.join(2)
    report['passed']=all(row['passed'] for row in report.values())
    output=ROOT/'reports/stream_lifecycle_regression.json';output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
