"""Register the standalone platform service under the installing desktop user."""
import pathlib,sys
root,user=sys.argv[1:3]
def quote(x):return '"'+x.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
text='''[Unit]
Description=Offline classroom drone visual navigation platform
After=local-fs.target
[Service]
Type=simple
User=%s
WorkingDirectory=%s
Environment=%s
Environment=HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 PYTHONNOUSERSITE=1 YOLO_OFFLINE=true YOLO_AUTOINSTALL=false WANDB_MODE=disabled
ExecStart=/bin/bash %s
Restart=no
KillMode=control-group
TimeoutStopSec=30
[Install]
WantedBy=multi-user.target
'''%(user,quote(root),quote('DRONENAV_ROOT='+root),quote(root+'/start.sh'))
pathlib.Path('/etc/systemd/system/dronenav-teaching.service').write_text(text)
