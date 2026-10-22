"""Configure a signed bundled Ubuntu kernel and an idempotent offline resume unit."""
import hashlib,pathlib,re,shutil,subprocess,sys
bundle,root,user=map(str,sys.argv[1:4])
no_start=len(sys.argv)>4 and sys.argv[4]=='1'
persist=pathlib.Path(root)/'state/installer';persist.mkdir(parents=True,exist_ok=True)
shutil.copy2(pathlib.Path(bundle)/'install.sh',persist/'install.sh')
shutil.copytree(pathlib.Path(bundle)/'tools',persist/'tools',dirs_exist_ok=True)
hashes=[]
for path in [persist/'install.sh']+sorted((persist/'tools').glob('*')):
    if path.is_file():hashes.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+str(path.relative_to(persist)))
(persist/'RESUME_SHA256SUMS').write_text('\n'.join(hashes)+'\n')
def quote(x):return '"'+x.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
unit='''[Unit]
Description=Complete the offline DroneNav installation after NVIDIA kernel reboot
After=local-fs.target systemd-udev-trigger.service
[Service]
Type=oneshot
ExecStart=/bin/bash %s --resume --prefix %s --user %s %s
TimeoutStartSec=0
[Install]
WantedBy=multi-user.target
'''%(quote(str(persist/'install.sh')),quote(root),quote(user),'--no-start' if no_start else '')
pathlib.Path('/etc/systemd/system/dronenav-install-resume.service').write_text(unit)
subprocess.run(['systemctl','enable','dronenav-install-resume.service'],check=True)
kernels=[]
for p in pathlib.Path(bundle+'/debs/driver').glob('linux-modules-nvidia-*-open-*_amd64.deb'):
    m=re.match(r'linux-modules-nvidia-\d+-open-(\d[^_]+)_',p.name)
    if m:kernels.append(m.group(1))
if not kernels:raise RuntimeError('No bundled precompiled NVIDIA kernel was found')
kernel=sorted(kernels,key=lambda s:[int(t) if t.isdigit() else t for t in re.split(r'(\d+)',s)])[-1]
if not pathlib.Path('/usr/sbin/update-grub').exists():raise RuntimeError('Ubuntu Desktop GRUB is required to select the bundled kernel automatically')
drop=pathlib.Path('/etc/default/grub.d/99-dronenav-offline.cfg');drop.parent.mkdir(exist_ok=True)
drop.write_text('GRUB_DEFAULT=saved\n')
subprocess.run(['update-grub'],check=True)
cfg=pathlib.Path('/boot/grub/grub.cfg').read_text()
entry=re.search(r"'(gnulinux-"+re.escape(kernel)+r"-advanced-[^']+)'",cfg)
submenu=re.search(r"'(gnulinux-advanced-[^']+)'",cfg)
if not entry or not submenu:raise RuntimeError('Cannot find the bundled kernel in generated GRUB configuration')
subprocess.run(['grub-set-default',submenu.group(1)+'>'+entry.group(1)],check=True)
(pathlib.Path(root)/'state/reboot-required.json').write_text(__import__('json').dumps({'kernel':kernel,'resume_configured':True},indent=2))
print('Selected bundled kernel:',kernel)
