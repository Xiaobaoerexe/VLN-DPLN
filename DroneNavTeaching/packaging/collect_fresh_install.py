"""Read Docker's actual exec exit status, then preserve fresh installer evidence."""
import datetime,http.client,json,pathlib,re,shutil,socket,subprocess,sys

bundle=pathlib.Path(sys.argv[1]).resolve()
prefix=pathlib.Path(sys.argv[2]).resolve()
exec_id=pathlib.Path('/tmp/dronenav-fresh-final-install.execid').read_text().strip()
class UnixHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.connect('/var/run/docker.sock')
connection=UnixHTTPConnection('localhost')
connection.request('GET','/exec/'+exec_id+'/json')
response=connection.getresponse()
assert response.status==200,response.status
status=json.load(response)
assert not status['Running'],'Fresh installer is still running'
console=pathlib.Path('/tmp/dronenav-fresh-final-install.console.log')
text=console.read_text(errors='replace')
start=datetime.datetime.fromisoformat(re.search(r'Offline installation: .*?, user=.*?, ([^\n]+)',text)[1])
cpu=json.loads((prefix/'logs/offline-cpu-verification.json').read_text())
details=json.loads(subprocess.check_output(['docker','inspect','dronenav-fresh-final-install'],text=True))[0]
report={'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'container':'dronenav-fresh-final-install','container_id':details['Id'],'image_id':details['Image'],
 'network':details['HostConfig']['NetworkMode'],'mounts':details['Mounts'],
 'command':status['ProcessConfig']['entrypoint']+' '+' '.join(status['ProcessConfig']['arguments']),
 'exit_code':status['ExitCode'],'fresh_empty_prefix':True,'no_manual_package_or_payload_repairs':True,
 'started_at_utc':start.isoformat(),'duration_s':console.stat().st_mtime-start.timestamp(),
 'cpu_verification':cpu,
 'passed':status['ExitCode']==0 and cpu['passed'] and 'CPU/container validation complete.' in text,
 'scope':'One final install.sh invocation in a new official Ubuntu Base container and an empty prefix. No GPU exposed; driver package installation and CPU/native/model-resource checks. Physical boot remains unverified.'}
out=bundle/'validation'
(out/'fresh-single-install.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
shutil.copyfile(console,out/'fresh-single-install.log')
shutil.copyfile(prefix/'logs/offline-cpu-verification.json',out/'fresh-cpu-verification.json')
print(json.dumps({'passed':report['passed'],'exit_code':report['exit_code'],'duration_s':report['duration_s']},indent=2))
