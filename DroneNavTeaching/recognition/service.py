"""Independent local-only recognition server. Start with recognition/.venv/bin/python."""
import argparse, base64, json, threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import cv2
import numpy as np
from core import RecognitionEngine

engine=None; lock=threading.Lock()
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_json({'status':'ready','model':'YOLO11s + DINOv2-S/14','port':self.server.server_port,
                        'gallery_n':len(engine.retriever.labels),'gallery_classes':len(set(engine.retriever.labels))})
    def do_POST(self):
        if self.path!='/infer':self.send_json({'error':'use /infer'},404);return
        try:
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            raw=np.frombuffer(base64.b64decode(data['jpeg']),np.uint8)
            bgr=cv2.imdecode(raw,cv2.IMREAD_COLOR)
            if bgr is None:raise ValueError('invalid JPEG')
            rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
            with lock:result=engine.detect(rgb,str(data.get('target','')))
            self.send_json(result)
        except Exception as e:
            traceback.print_exc();self.send_json({'error':str(e)},500)
    def send_json(self,result,status=200):
        out=json.dumps(result).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(out)))
        self.end_headers();self.wfile.write(out)
    def log_message(self,*args):pass

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8766);p.add_argument('--device',default='cuda:0');a=p.parse_args()
    engine=RecognitionEngine(a.device)
    print('recognition ready on 127.0.0.1:%d'%a.port,flush=True)
    ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()
