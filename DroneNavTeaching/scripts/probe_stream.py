"""Read-only local HTTP state/map/MJPEG delivery measurements."""
import json
from pathlib import Path
import re
import statistics
import time
import requests

ROOT=Path(__file__).resolve().parents[1]


def main():
    report={}
    session=requests.Session()
    for name,path,count in [('state','/api/state',15),('map','/api/map',4)]:
        rows=[]
        for i in range(count):
            start=time.perf_counter();r=session.get('http://127.0.0.1:5800'+path,timeout=5);r.raise_for_status()
            rows.append({'roundtrip_ms':(time.perf_counter()-start)*1000,'bytes':len(r.content)})
            time.sleep(.035)
        report[name]={'samples':count,'mean_roundtrip_ms':statistics.mean(x['roundtrip_ms'] for x in rows),
                      'max_roundtrip_ms':max(x['roundtrip_ms'] for x in rows),
                      'mean_payload_bytes':statistics.mean(x['bytes'] for x in rows)}
    state=session.get('http://127.0.0.1:5800/api/state',timeout=5).json()
    report['status']=state['status'];report['run_id']=state.get('run_id')
    report['state_has_map_points']='map_points' in state
    for view in ['front','overview']:
        response=session.get('http://127.0.0.1:5800/api/stream.mjpg?view='+view,stream=True,timeout=8)
        begin=time.perf_counter();pending=b'';arrivals=[];seqs=[];total=0
        for chunk in response.iter_content(chunk_size=4096):
            total+=len(chunk);pending+=chunk
            while True:
                start=pending.find(b'--frame\r\n')
                header_end=pending.find(b'\r\n\r\n',max(0,start))
                if start<0 or header_end<0:break
                header=pending[start:header_end]
                match=re.search(br'Content-Length: (\d+)',header)
                size=int(match.group(1))
                if len(pending)<header_end+4+size+2:break
                seq=re.search(br'X-Frame-Seq: (\d+)',header)
                seqs.append(int(seq.group(1)));arrivals.append(time.perf_counter())
                pending=pending[header_end+4+size+2:]
            if time.perf_counter()-begin>2.5:break
        response.close()
        report[view+'_stream']={'frames':len(arrivals),'unique_seq':len(set(seqs)),'bytes':total,
                               'measured_delivery_hz':(len(arrivals)-1)/(arrivals[-1]-arrivals[0]) if len(arrivals)>1 else None,
                               'elapsed_s':time.perf_counter()-begin}
    path=ROOT/'reports/http_stream_probe.json';path.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
