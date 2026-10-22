"""Balanced full-frame RGB-only smoke, separate from crop retrieval acceptance.

Offline goal pixels are used only after inference to score point-in-box recall.
No pose or target pixel is transmitted to the recognition service.
"""
import collections,hashlib,json,time
from pathlib import Path
import cv2
import numpy as np
from sample_dataset import simulator,pose,ROOT
from client import RecognitionClient

d=json.loads((ROOT/'assets/recognition/dataset/manifest.json').read_text())
groups=collections.defaultdict(list)
for x in d['records']:
    if x['split']=='test':groups[x['label']].append(x)
selected=[x for c in sorted(groups) for x in groups[c][:2]]
scenes=collections.defaultdict(list)
for x in selected:scenes[x['scene']].append(x)
client=RecognitionClient();rows=[];begin=time.time();example=False
for scene,items in sorted(scenes.items()):
    sim=simulator(scene)
    for r in items:
        sim.get_agent(0).set_state(pose(r),reset_sensors=True)
        rgb=sim.get_sensor_observations()['rgb'][:,:,:3].copy()
        out=client.detect(rgb,r['label']);u,v=r['target_pixel']
        matches=[b for b in out['boxes'] if b['is_target']]
        contained=[b for b in matches if b['xyxy'][0]<=u<=b['xyxy'][2] and b['xyxy'][1]<=v<=b['xyxy'][3]]
        rows.append({'image_id':r['image'],'target':r['label'],'scene':scene,'target_class_hit':bool(matches),
                     'target_centre_in_predicted_box':bool(contained),'latency_ms':out['latency_ms'],'boxes':out['boxes']})
        if not example and any(b['source']=='DINOv2 gallery' for b in contained):
            im=cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR)
            for b in out['boxes']:
                x1,y1,x2,y2=b['xyxy'];color=(0,255,255) if b['is_target'] else (90,180,90)
                cv2.rectangle(im,(x1,y1),(x2,y2),color,2)
                cv2.putText(im,'%s %.2f'%(b['label'],b['score']),(x1,max(15,y1+15)),cv2.FONT_HERSHEY_SIMPLEX,.4,color,1)
            cv2.imwrite(str(ROOT/'reports/recognition_runtime_gallery.jpg'),im);example=True
    sim.close();print('runtime frames',len(rows),len(selected),flush=True)
report={'metric':'balanced full-frame target-class / annotated-centre recall smoke; not detection mAP',
        'gallery_sha256':hashlib.sha256((ROOT/'assets/recognition/gallery.npz').read_bytes()).hexdigest(),
        'n':len(rows),'classes':len(groups),'target_class_hit_n':sum(x['target_class_hit'] for x in rows),
        'target_class_hit_rate':sum(x['target_class_hit'] for x in rows)/len(rows),
        'target_centre_in_predicted_box_n':sum(x['target_centre_in_predicted_box'] for x in rows),
        'target_centre_in_predicted_box_rate':sum(x['target_centre_in_predicted_box'] for x in rows)/len(rows),
        'mean_inference_ms':float(np.mean([x['latency_ms'] for x in rows])),
        'duration_s':time.time()-begin,'predictions':rows}
(ROOT/'reports/recognition_runtime_balanced.json').write_text(json.dumps(report,indent=2))
print(json.dumps({k:v for k,v in report.items() if k!='predictions'},indent=2),flush=True)
