"""Collect read-only package/container evidence after the offline test runs."""
import datetime,hashlib,json,pathlib,subprocess,sys

bundle=pathlib.Path(sys.argv[1]).resolve()
def command(args):
    r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    return {'returncode':r.returncode,'stdout':r.stdout.strip(),'stderr':r.stderr.strip()}
def inside(name,args):return command(['docker','exec',name]+args)
def package_rows(name):
    result=inside(name,['dpkg-query','-W','-f=${Package}\t${Version}\t${Status}\n'])
    return [line.split('\t',2) for line in result['stdout'].splitlines() if line.count('\t')==2]
clean=sys.argv[2] if len(sys.argv)>2 else 'dronenav-clean-install';gpu='dronenav-offline-validation'
rows=package_rows(clean);gpu_rows=package_rows(gpu)
driver=[r for r in rows if any(x in r[0] for x in ['nvidia','linux-image','linux-modules','linux-headers'])]
compiler=[r for r in gpu_rows if r[0] in ['gcc','g++','libc6-dev','gcc-13','g++-13','libstdc++-13-dev']]
module=inside(clean,['find','/lib/modules','-name','nvidia.ko*'])['stdout'].splitlines()
signers=[inside(clean,['modinfo','-F','signer',path]) for path in module]
base=bundle/'preparation/ubuntu-base-24.04.4-amd64.tar.gz'
expected='c1e67ef7b17a6300e136118bd1dc04725009cb376c1aad10abcf8cd453628d58'
actual=hashlib.sha256(base.read_bytes()).hexdigest() if base.is_file() else None
audit=inside(clean,['dpkg','--audit'])
report={'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'clean_os':inside(clean,['cat','/etc/os-release']),
 'base_rootfs':{'url':'https://mirrors.tuna.tsinghua.edu.cn/ubuntu-cdimage/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.4-base-amd64.tar.gz','sha256':actual,'official_expected_sha256':expected,'matches':actual==expected},
 'clean_container':command(['docker','inspect',clean,'--format','{{json .HostConfig.NetworkMode}} {{json .Mounts}}']),
 'gpu_container':command(['docker','inspect',gpu,'--format','{{json .HostConfig.NetworkMode}} {{json .HostConfig.DeviceRequests}} {{json .Mounts}}']),
 'driver_packages':driver,'dpkg_audit':audit,'module_signatures':signers,
 'dkms_status':inside(clean,['dkms','status']),
 'runtime_compiler_packages':compiler,
 'gpu_runtime_nvidia595_packages':[r for r in gpu_rows if '595' in r[0] and 'nvidia' in r[0]],
 'physical_boot_secure_boot':{'verified':False,'reason':'Containers cannot activate a new host kernel or validate firmware/Secure Boot. Shared host driver and kernel were not modified.'}}
report['passed']=bool(actual==expected and audit['returncode']==0 and not audit['stdout'] and driver and signers and all(s['returncode']==0 and 'Canonical' in s['stdout'] for s in signers) and not report['gpu_runtime_nvidia595_packages'] and len(compiler)==6 and not any('nvidia-dkms' in r[0] for r in driver))
path=bundle/'validation/clean-package-driver-evidence.json'
path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({'path':str(path),'passed':report['passed'],'runtime_compilers':compiler,'driver_packages':len(driver)},indent=2))
