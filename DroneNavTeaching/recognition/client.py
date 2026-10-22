"""Python 3.8 compatible client. Call in a worker; errors are explicit."""
import base64,json,time,urllib.request
import cv2

class RecognitionClient:
    def __init__(self,url='http://127.0.0.1:8766',timeout=15):
        self.url=url.rstrip('/');self.timeout=timeout
    def detect(self,rgb,target):
        started=time.perf_counter()
        ok,jpeg=cv2.imencode('.jpg',cv2.cvtColor(rgb[:,:,:3],cv2.COLOR_RGB2BGR),[cv2.IMWRITE_JPEG_QUALITY,90])
        if not ok:raise ValueError('could not encode image')
        body=json.dumps({'jpeg':base64.b64encode(jpeg.tobytes()).decode('ascii'),'target':target}).encode()
        req=urllib.request.Request(self.url+'/infer',data=body,headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=self.timeout) as resp:result=json.load(resp)
        if 'error' in result:raise RuntimeError(result['error'])
        result['roundtrip_ms']=(time.perf_counter()-started)*1000.
        return result
