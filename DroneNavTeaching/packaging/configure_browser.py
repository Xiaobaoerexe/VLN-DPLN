"""Run as the target desktop user. Preserve an existing valid default browser."""
import json,os,pathlib,subprocess,sys
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root))
no_start=len(sys.argv)>2 and sys.argv[2]=='1'
from scripts.manage import _desktop_env
from scripts.open_browser_once import register_deferred_browser
env=_desktop_env();result={'registered':False,'existing_default_preserved':False}
if not env.get('DISPLAY') and not env.get('WAYLAND_DISPLAY'):
 result['reason']='No desktop session; standalone Firefox remains available as launcher fallback.'
 result['login_once']=register_deferred_browser(root,enabled=not no_start)
else:
 result['login_once']=register_deferred_browser(root,enabled=False)
 r=subprocess.run(['xdg-mime','query','default','x-scheme-handler/http'],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 default=r.stdout.strip();result['detected_default']=default
 dirs=[pathlib.Path(env.get('XDG_DATA_HOME',str(pathlib.Path.home()/'.local/share')))]+[pathlib.Path(x) for x in env.get('XDG_DATA_DIRS','/usr/local/share:/usr/share').split(':')]
 exists=bool(default) and any((x/'applications'/default).is_file() for x in dirs)
 if exists:result['existing_default_preserved']=True
 else:
  path=pathlib.Path.home()/'.local/share/applications/dronenav-firefox.desktop';path.parent.mkdir(parents=True,exist_ok=True)
  binary=str(root/'browser/firefox/firefox').replace('\\','\\\\').replace('"','\\"').replace('%','%%')
  path.write_text('[Desktop Entry]\nType=Application\nName=DroneNav Offline Firefox\nExec="'+binary+'" --new-window %u\nTerminal=false\nCategories=Network;WebBrowser;\nMimeType=text/html;x-scheme-handler/http;x-scheme-handler/https;\n')
  for mime in ['text/html','x-scheme-handler/http','x-scheme-handler/https']:
   subprocess.run(['xdg-mime','default',path.name,mime],env=env,check=True)
  result['registered']=True
(root/'logs/browser-registration.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
