"""Offline verification for a relocated prefix. Can run in clean CPU-only Ubuntu."""
import argparse,hashlib,json,os,pathlib,subprocess,sys,time
p=argparse.ArgumentParser();p.add_argument('prefix');p.add_argument('--cpu',action='store_true');p.add_argument('--gpu',action='store_true');a=p.parse_args()
root=pathlib.Path(a.prefix).resolve();env=dict(os.environ,PYTHONPATH=str(root),PYTHONNOUSERSITE='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_DATASETS_OFFLINE='1',DRONENAV_ROOT=str(root),DRONENAV_DATA_ROOT=str(root/'data'),MAGNUM_LOG='quiet',HABITAT_SIM_LOG='quiet',YOLO_OFFLINE='true',YOLO_AUTOINSTALL='false',WANDB_MODE='disabled')
if not a.gpu:env['CUDA_VISIBLE_DEVICES']=''
checks={};started=time.time()
def run(name,python,code):
    out=subprocess.run([str(root/python),'-c',code],cwd=str(root),env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
    checks[name]={'returncode':out.returncode,'stdout':out.stdout[-4000:],'stderr':out.stderr[-4000:]}
    if out.returncode:raise RuntimeError(name+' failed: '+out.stderr[-2000:])
try:
    assert len(list((root/'data/datasets/ovon/hm3d/val_unseen/content').glob('[!.]*.json.gz')))==36
    depth=root/'vendor/InternNav/checkpoints/depth_anything_v2_metric_hypersim_vits.pth'
    assert depth.is_file(),'Required DepthAnything initialization weight is missing'
    digest=hashlib.sha256(depth.read_bytes()).hexdigest()
    assert depth.stat().st_size==99222290 and digest=='b782898d8a3e8be1f639de33837ed85e9b4b73e40f8f5e5cd99067588d722545','DepthAnything checkpoint integrity mismatch'
    checks['required_depth_anything_weight']={'bytes':depth.stat().st_size,'sha256':digest}
    run('ovon_imports','runtime/ovon/bin/python','import sys,numpy,cv2,habitat_sim,quaternion;import app.server;from app.simulator import EpisodeCatalog; c=EpisodeCatalog();print(sys.executable,len(c.files));print({m.__name__:m.__file__ for m in [numpy,cv2,habitat_sim,quaternion,app.server]})')
    run('navigation_imports','runtime/internnav/bin/python',"import sys;from pathlib import Path;sys.path.insert(0,str(Path('vendor/InternNav').resolve()));import torch,transformers,flash_attn,internnav;from internnav.agent.internvla_n1_agent_realworld import InternVLAN1AsyncAgent;print(sys.executable,torch.__version__,transformers.__version__)")
    run('local_model_and_tokenizer','runtime/internnav/bin/python',"from transformers import AutoProcessor;from pathlib import Path;import json;p=Path('vendor/InternNav/checkpoints/InternVLA-N1-DualVLN');c=json.load(open(p/'config.json'));t=AutoProcessor.from_pretrained(str(p),local_files_only=True);d=json.load(open(p/'model.safetensors.index.json'));s=sorted(set(d['weight_map'].values()));assert len(s)==4 and all((p/x).is_file() for x in s);print(c['model_type'],len(t.tokenizer),sum((p/x).stat().st_size for x in s))")
    run('recognition_cpu_retrieval','runtime/internnav/bin/python',"import sys,json,cv2;sys.path.insert(0,'recognition');from core import ImageRetriever,ASSETS;d=json.load(open(ASSETS/'dataset/manifest.json'));r=next(x for x in d['records'] if x['split']=='test');im=cv2.cvtColor(cv2.imread(str(ASSETS/'dataset'/r['image'])),cv2.COLOR_BGR2RGB);m=ImageRetriever('cpu');print(m.search([im]),len(m.labels));from ultralytics import YOLO;YOLO(str(ASSETS/'yolo11s.pt'));print('YOLO local weights OK')")
    libs=[root/'vendor/ORB_SLAM3/lib',root/'vendor/ORB_SLAM3/Thirdparty/DBoW2/lib',root/'vendor/ORB_SLAM3/Thirdparty/g2o/lib',root/'vendor/pangolin-install/lib']
    native=subprocess.run(['ldd',str(root/'localization/build/orb_ipc')],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=dict(env,LD_LIBRARY_PATH=':'.join(map(str,libs))))
    checks['slam_dynamic_libraries']={'returncode':native.returncode,'unresolved':[x.strip() for x in native.stdout.splitlines() if 'not found' in x]}
    assert native.returncode==0 and not checks['slam_dynamic_libraries']['unresolved']
    run('slam_native_worker','runtime/ovon/bin/python','from localization.orb_slam3 import ORBSLAM3Localizer;s=ORBSLAM3Localizer();print(s.reset([0,1.4,0],[1,0,0,0]));s.close();print("Native ORB vocabulary/config/start/close OK")')
    if a.gpu:
        run('gpu_torch_and_capacity','runtime/internnav/bin/python',"import torch,json;from flash_attn import flash_attn_func;assert torch.cuda.is_available();p=torch.cuda.get_device_properties(0);cap=torch.cuda.get_device_capability();assert cap in [(8,0),(8,6),(8,9),(9,0)],'Bundle requires supported Ampere/Ada/Hopper CUDA architecture, not Turing or Blackwell';assert p.total_memory>=30*1024**3,'At least 32GB nominal VRAM required';x=torch.ones((1,16,2,32),device='cuda',dtype=torch.float16);y=flash_attn_func(x,x,x);print(json.dumps({'gpu':p.name,'vram_bytes':p.total_memory,'compute_capability':list(cap),'flash_attention_gpu_result':float(y.sum())}))")
        run('gpu_habitat_render','runtime/ovon/bin/python',"from app.simulator import EpisodeCatalog,DroneSimulator;c=EpisodeCatalog();s=DroneSimulator(c.choose(seed=42),seed=42,render_overview=False);o=s.observe();print(type(o));s.close()")
    report={'passed':True,'gpu_mode':a.gpu,'prefix':str(root),'checks':checks,'duration_s':time.time()-started}
except Exception as exc:
    report={'passed':False,'gpu_mode':a.gpu,'prefix':str(root),'checks':checks,'error':str(exc),'duration_s':time.time()-started}
print(json.dumps(report,indent=2));sys.exit(0 if report['passed'] else 1)
