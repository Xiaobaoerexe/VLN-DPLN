"""Run inside the network-none runtime container with the matching host driver."""
import json,os,pathlib,socket,subprocess,sys,time,urllib.request
root=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
env=dict(os.environ,DRONENAV_ROOT=str(root),DRONENAV_DATA_ROOT=str(root/'data'),PYTHONPATH=str(root),PYTHONNOUSERSITE='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_DATASETS_OFFLINE='1',YOLO_OFFLINE='true',YOLO_AUTOINSTALL='false',WANDB_MODE='disabled')
python=str(root/'runtime/ovon/bin/python');report={'prefix':str(root),'network':'Docker --network none; loopback only','host_driver_used':True,'checks':{}}
started=time.time();child=None
def gpu_snapshot():
    processes=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,process_name,used_gpu_memory','--format=csv,noheader'],text=True).strip()
    memory=subprocess.check_output(['nvidia-smi','--query-gpu=name,driver_version,memory.used,memory.total','--format=csv,noheader'],text=True).strip()
    return {'processes':processes.splitlines() if processes else [],'memory':memory}
try:
    report['gpu_before']=gpu_snapshot()
    verify=subprocess.run(['python3','/bundle/tools/verify_install.py',str(root),'--gpu'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=240)
    (out/'relocated-gpu-verification.json').write_text(verify.stdout)
    assert verify.returncode==0,verify.stdout+verify.stderr
    report['checks']['imports_fa2_habitat_gpu']=True
    log=(out/'relocated-supervisor.log').open('w')
    child=subprocess.Popen(['bash',str(root/'start.sh'),'--no-browser','--timeout','240'],cwd=str(root),env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    ready={}
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        if child.poll() is not None:raise RuntimeError('Supervisor exited before readiness; inspect relocated-supervisor.log')
        for name,port,path in [('navigation',5811,'/health'),('recognition',8766,'/health'),('platform',5800,'/api/health')]:
            try:
                with urllib.request.urlopen('http://127.0.0.1:%d%s'%(port,path),timeout=2) as r:ready[name]=json.load(r)
            except Exception:continue
        if len(ready)==3 and ready['navigation'].get('ready') and ready['recognition'].get('status')=='ready':break
        time.sleep(.25)
    else:raise TimeoutError('Models failed to become ready')
    report['checks']['all_three_services_ready']=True;report['ready']=ready
    result=subprocess.run([python,str(root/'scripts/verify_platform.py')],cwd=str(root),env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=420)
    (out/'relocated-platform-test.log').write_text(result.stdout+'\n'+result.stderr)
    assert result.returncode==0,result.stdout+result.stderr
    app_report=json.loads((root/'reports/ui_acceptance.json').read_text());report['platform_lifecycle']=app_report
    assert app_report['passed']
    child.wait(timeout=35);report['checks']['supervisor_exited_after_api_exit']=child.returncode==0
    active=[]
    for port in [5800,5811,8766]:
        s=socket.socket();s.settimeout(.5)
        if s.connect_ex(('127.0.0.1',port))==0:active.append(port)
        s.close()
    report['checks']['all_ports_closed']=not active
    report['gpu_after']=gpu_snapshot()
    report['checks']['no_gpu_compute_processes_after_exit']=not report['gpu_after']['processes']
    report['passed']=all(report['checks'].values())
except Exception as exc:
    report['passed']=False;report['error']=repr(exc)
finally:
    if child and child.poll() is None:
        subprocess.run(['bash',str(root/'stop.sh')],cwd=str(root),env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40)
        try:child.wait(timeout=15)
        except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=10)
    report['duration_s']=time.time()-started
    (out/'relocated-lifecycle.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2));sys.exit(0 if report['passed'] else 1)
