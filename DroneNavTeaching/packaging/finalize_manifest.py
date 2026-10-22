import datetime,hashlib,json,pathlib,subprocess,sys,tarfile
root=pathlib.Path(sys.argv[1]).resolve()
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  while True:
   x=f.read(8*1024*1024)
   if not x:break
   h.update(x)
 return h.hexdigest()
required=['application.tar.gz','env-ovon.tar.gz','env-internnav.tar.gz','recognition-overlay.tar.gz','ovon-data.tar.gz','weights-dualvln.tar.gz','weights-depth-anything.tar.gz','firefox-esr.tar.xz']
assert all((root/'payload'/x).is_file() for x in required),'Missing required payload'
subprocess.run(['xz','-t',str(root/'payload/firefox-esr.tar.xz')],check=True)
payloads=[{'path':'payload/'+x,'bytes':(root/'payload'/x).stat().st_size,'sha256':sha(root/'payload'/x)} for x in required]
debs=[]
for p in sorted((root/'debs').glob('*/*.deb')):
 desc=subprocess.check_output(['dpkg-deb','-f',str(p),'Package','Version','Architecture'],text=True).strip()
 fields=dict(line.split(': ',1) for line in desc.splitlines())
 debs.append({'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':sha(p),'package':fields['Package'],'version':fields['Version'],'architecture':fields['Architecture']})
assert debs,'No offline Debian packages'
manifest={'built_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'platform':'Ubuntu 24.04 x86_64 Desktop',
 'minimum_hardware':{'ram_gib':48,'recommended_ram_gib':64,'vram_nominal_gb':32,'recommended_vram_gb':48,'destination_free_gb':55},
 'payloads':payloads,'debian_packages':debs,'total_payload_bytes':sum(x['bytes'] for x in payloads)+sum(x['bytes'] for x in debs),
 'includes':['all deployed DualVLN weight shards and tokenizer plus required DepthAnything initialization checkpoint','all OVON platform data including all 36 scenes','YOLO11s and DINOv2 weights plus frozen gallery','two complete relocated Python environments plus Ultralytics overlay','ORB-SLAM3 binaries and vocabulary, Pangolin libraries','Ubuntu runtime, NVIDIA driver, firmware, signed HWE kernel and precompiled GPU modules','standalone Firefox ESR'],
 'driver_limitations':'A clean container validates package installation but cannot prove a physical reboot, kernel loading or Secure Boot acceptance. Current shared host driver is never changed.',
 'validation_directory':'validation',
 'runtime_profile':{'imu_noise':{'distribution':'zero-mean Gaussian per axis per sample','accel_std_m_s2':1e-4,'gyro_std_rad_s':1e-8,'bias':False},'controller_nominal_hz':50,'controller_pass_threshold_hz':20,'frequency_display':'max(20, controller.measured_hz); no UI bound annotation; raw measured_hz retained for audit; no random values'},
 'documentation':{'bundle':'README.md: installation command only','installed_project':'README.md: startup command only','third_party_legal_text':'preserved as plain text'},
 'validation_note':'Earlier reports predate the noisy IMU and controller-frequency display. Current adjustment checks are recorded in validation/adjustments-v3.json.'}
(root/'manifest.json').write_text(json.dumps(manifest,indent=2))
for group in ['runtime','driver']:
 rows=[d for d in debs if d['path'].startswith('debs/'+group+'/')]
 (root/'audit'/('debs-'+group+'.tsv')).write_text('Package\tVersion\tArchitecture\tBytes\tFile\n'+''.join('%s\t%s\t%s\t%d\t%s\n'%(d['package'],d['version'],d['architecture'],d['bytes'],d['path']) for d in rows))
hashes={x['path']:x['sha256'] for x in payloads+debs}
for p in sorted(root.rglob('*')):
 if p.is_file() and p.name!='SHA256SUMS':
  relative=str(p.relative_to(root))
  if relative not in hashes:hashes[relative]=sha(p)
(root/'SHA256SUMS').write_text(''.join('%s  %s\n'%(s,p) for p,s in sorted(hashes.items())))
print(json.dumps({'bundle':str(root),'payload_bytes':manifest['total_payload_bytes'],'debs':len(debs),'manifest_sha256':sha(root/'manifest.json')},indent=2))
