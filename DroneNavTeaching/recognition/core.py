"""YOLO11s proposals + frozen DINOv2-S/14 known-environment image retrieval.

Runtime has no simulator imports, poses, semantic IDs or ground truth labels.
"""
import json, time
from pathlib import Path
import cv2
import numpy as np
import torch
from transformers import AutoModel
from ultralytics import YOLO

ROOT=Path(__file__).resolve().parents[1]
ASSETS=ROOT/'assets/recognition'

class ImageRetriever:
    def __init__(self, device='cuda:0', load_gallery=True):
        torch.set_num_threads(2)
        self.device=device
        self.model=AutoModel.from_pretrained(str(ASSETS/'dinov2-small'),local_files_only=True).to(device).eval()
        self.mean=torch.tensor([.485,.456,.406],device=device)[None,:,None,None]
        self.std=torch.tensor([.229,.224,.225],device=device)[None,:,None,None]
        self.labels=[]; self.gallery=None
        if load_gallery:
            d=np.load(str(ASSETS/'gallery.npz'),allow_pickle=False)
            self.labels=list(d['labels']); self.gallery=torch.tensor(d['features'],device=device)

    @torch.inference_mode()
    def embed(self, images):
        if not images:return np.empty((0,384),np.float32)
        a=np.stack([cv2.resize(x,(224,224),interpolation=cv2.INTER_CUBIC) for x in images])
        t=torch.from_numpy(a).permute(0,3,1,2).to(self.device,dtype=torch.float32)/255.
        x=self.model(pixel_values=(t-self.mean)/self.std).last_hidden_state[:,0]
        return torch.nn.functional.normalize(x,dim=-1).float().cpu().numpy()

    def search(self, images):
        f=torch.from_numpy(self.embed(images)).to(self.device)
        scores=f @ self.gallery.T
        vals,idx=scores.max(dim=1)
        return [(self.labels[i],float(s)) for i,s in zip(idx.tolist(),vals.tolist())]

class RecognitionEngine:
    def __init__(self,device='cuda:0'):
        self.device=device
        self.yolo=YOLO(str(ASSETS/'yolo11s.pt'))
        self.retriever=ImageRetriever(device)
        self.total=0; self.started=time.monotonic(); self.last_ms=0.

    def detect(self,rgb,target):
        start=time.perf_counter(); h,w=rgb.shape[:2]
        result=self.yolo.predict(source=rgb[:,:,::-1].copy(),imgsz=640,conf=.25,device=self.device,verbose=False)[0]
        boxes=[]; proposals=[]
        for b in result.boxes:
            xy=[int(x) for x in b.xyxy[0].tolist()]; label=result.names[int(b.cls[0])]
            boxes.append({'xyxy':xy,'label':label,'score':float(b.conf[0]),'score_type':'detector_confidence','source':'YOLO11s','is_target':matches(label,target)})
            if xy[2]>xy[0]+12 and xy[3]>xy[1]+12:proposals.append(xy)
        # Fixed region proposals cover objects outside YOLO's 80-class vocabulary.
        # Their rectangles are retrieval windows, not instance segmentation masks.
        side=min(224,h,w)
        xs=np.linspace(0,w-side,4).astype(int);ys=np.linspace(0,h-side,3).astype(int)
        proposals += [[int(x),int(y),int(x+side),int(y+side)] for y in ys for x in xs]
        crops=[rgb[y1:y2,x1:x2] for x1,y1,x2,y2 in proposals]
        found=self.retriever.search(crops)
        candidates=[]
        for xy,(label,score) in zip(proposals,found):
            if score>=.72:
                candidates.append({'xyxy':xy,'label':label,'score':score,'score_type':'cosine_similarity','source':'DINOv2 gallery','is_target':matches(label,target)})
        # Prefer matching target vocabulary; retain other high-score recognized objects.
        candidates.sort(key=lambda b:(b['is_target'],b['score']),reverse=True)
        keep=[]
        for b in candidates:
            # One best retrieval window per class keeps the classroom view legible.
            # Prefer a YOLO instance box when that class has already been detected.
            if any(matches(a['label'],b['label']) for a in boxes):continue
            if any(b['label']==a['label'] for a in keep):continue
            keep.append(b)
            if len(keep)>=5:break
        boxes=keep+boxes[:8]
        self.last_ms=(time.perf_counter()-start)*1000.;self.total+=1
        return {'boxes':boxes,'latency_ms':self.last_ms,'hz':1000./max(self.last_ms,.001),
                'throughput_hz':self.total/max(time.monotonic()-self.started,.001),
                'model':'YOLO11s + DINOv2-S/14 gallery','target':target,'frame_shape':[h,w],
                'retrieval_box_type':'fixed window or YOLO proposal; approximate localization'}

ALIASES={'potted plant':{'plant','decorative plant'},'vase':{'flower vase'},
         'couch':{'sofa'},'book':{'books'}}
def matches(pred,target):
    a=pred.lower().strip();b=target.lower().strip()
    return a==b or b in ALIASES.get(a,set())

def iou(a,b):
    x=max(0,min(a[2],b[2])-max(a[0],b[0]));y=max(0,min(a[3],b[3])-max(a[1],b[1]))
    v=x*y;return v/max((a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-v,1)
