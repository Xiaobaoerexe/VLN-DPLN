"""Build frozen gallery and evaluate disjoint view crop retrieval + zero-shot YOLO."""
import collections,hashlib,json,time
from pathlib import Path
import cv2
import numpy as np
from core import ASSETS,ROOT,ImageRetriever,YOLO,matches

def main():
    d=json.loads((ASSETS/'dataset/manifest.json').read_text());r=d['records']
    gallery=[x for x in r if x['split']=='gallery'];tests=[x for x in r if x['split']=='test']
    def read(x):return cv2.cvtColor(cv2.imread(str(ASSETS/'dataset'/x['image'])),cv2.COLOR_BGR2RGB)
    gallery_hashes={hashlib.sha256(read(x).tobytes()).hexdigest() for x in gallery}
    tests=[x for x in tests if hashlib.sha256(read(x).tobytes()).hexdigest() not in gallery_hashes]
    retriever=ImageRetriever(load_gallery=False); feats=[]
    for k in range(0,len(gallery),32):feats.append(retriever.embed([read(x) for x in gallery[k:k+32]]))
    features=np.concatenate(feats)
    labels=np.array([x['label'] for x in gallery]);np.savez_compressed(str(ASSETS/'gallery.npz'),features=features,labels=labels,images=np.array([x['image'] for x in gallery]))
    predictions=[];begin=time.time()
    for k in range(0,len(tests),32):
        f=retriever.embed([read(x) for x in tests[k:k+32]]);scores=f@features.T
        for x,sc in zip(tests[k:k+32],scores):
            idx=int(sc.argmax());predictions.append({'image':x['image'],'true':x['label'],'pred':str(labels[idx]),'score':float(sc[idx]),'reference':gallery[idx]['image'],'correct':bool(labels[idx]==x['label'])})
    retrieval_s=time.time()-begin
    yolo=YOLO(str(ASSETS/'yolo11s.pt'));ys=[];begin=time.time()
    for k in range(0,len(tests),16):
        chunk=tests[k:k+16];res=yolo.predict([read(x)[:,:,::-1].copy() for x in chunk],imgsz=640,conf=.25,device='cuda:0',verbose=False)
        for x,b in zip(chunk,res):
            names=[b.names[int(c)] for c in b.boxes.cls.tolist()]
            ys.append({'image':x['image'],'true':x['label'],'predicted_classes':names,'correct':any(matches(c,x['label']) for c in names)})
    by_class={}
    for label in sorted(set(x['label'] for x in tests)):
        ps=[x for x in predictions if x['true']==label];zs=[x for x in ys if x['true']==label]
        by_class[label]={'n':len(ps),'retrieval_correct':sum(x['correct'] for x in ps),'retrieval_accuracy':sum(x['correct'] for x in ps)/len(ps),'yolo_correct':sum(x['correct'] for x in zs)}
    report={'metric':'top-1 known-environment image retrieval on goal-centred crops; not detection mAP',
        'gallery_n':len(gallery),'test_n':len(tests),'classes':len(by_class),
        'gallery_test_exact_pixel_duplicates_excluded':len([x for x in r if x['split']=='test'])-len(tests),
        'retrieval_correct':sum(x['correct'] for x in predictions),'retrieval_accuracy':sum(x['correct'] for x in predictions)/len(predictions),
        'retrieval_macro_accuracy':float(np.mean([v['retrieval_accuracy'] for v in by_class.values()])),
        'scenes_sampled':len(set(x['scene'] for x in r)),
        'object_instances':len(set(x['instance'] for x in r)),
        'gallery_test_instance_overlap':len({x['instance'] for x in gallery}&{x['instance'] for x in tests}),
        'gallery_sha256':hashlib.sha256((ASSETS/'gallery.npz').read_bytes()).hexdigest(),
        'manifest_sha256':hashlib.sha256((ASSETS/'dataset/manifest.json').read_bytes()).hexdigest(),
        'retrieval_seconds':retrieval_s,'yolo_zero_shot_correct':sum(x['correct'] for x in ys),'yolo_zero_shot_accuracy':sum(x['correct'] for x in ys)/len(ys),
        'yolo_seconds':time.time()-begin,'per_class':by_class,'split':d['split'],
        'limitations':['same annotated object instances can appear in both sets, at distinct poses','crops use OVON goal centre offline; runtime uses RGB-only YOLO and fixed-grid proposals','OVON missing semantic meshes: no detection recall or localization mAP claim','task labels with ambiguous synonyms are kept distinct for retrieval'],
        'retrieval_predictions':predictions,'yolo_predictions':ys}
    (ROOT/'reports/recognition_acceptance.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ['per_class','retrieval_predictions','yolo_predictions']},indent=2),flush=True)

if __name__=='__main__':main()
