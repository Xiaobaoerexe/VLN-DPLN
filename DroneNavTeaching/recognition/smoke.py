"""End-to-end RGB-only HTTP client smoke using a reproducible OVON camera pose."""
import json,time
from pathlib import Path
import cv2
import numpy as np
from sample_dataset import simulator,pose,ROOT
from client import RecognitionClient

d=json.loads((ROOT/'assets/recognition/dataset/manifest.json').read_text())
r=next(x for x in d['records'] if x['label']=='microwave' and x['split']=='test')
sim=simulator(r['scene']);sim.get_agent(0).set_state(pose(r),reset_sensors=True)
rgb=sim.get_sensor_observations()['rgb'][:,:,:3].copy();sim.close()
client=RecognitionClient();results=[]
for i in range(6):
    results.append(client.detect(rgb,r['label']))
    time.sleep(.1)
out=cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR)
for b in results[-1]['boxes']:
    x1,y1,x2,y2=b['xyxy'];colour=(0,255,255) if b['source'].startswith('DINO') else (40,220,30)
    cv2.rectangle(out,(x1,y1),(x2,y2),colour,2)
    cv2.putText(out,'%s %.2f'%(b['label'],b['score']),(x1,max(y1+15,15)),cv2.FONT_HERSHEY_SIMPLEX,.45,colour,1)
cv2.imwrite(str(ROOT/'reports/recognition_runtime.jpg'),out)
report={'target':r['label'],'scene':r['scene'],'request_n':len(results),
        'warmup_roundtrip_ms':results[0]['roundtrip_ms'],
        'mean_roundtrip_ms_after_warmup':float(np.mean([x['roundtrip_ms'] for x in results[1:]])),
        'mean_inference_ms_after_warmup':float(np.mean([x['latency_ms'] for x in results[1:]])),
        'result':results[-1]}
(ROOT/'reports/recognition_runtime.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
