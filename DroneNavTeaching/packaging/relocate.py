"""Modify only the newly extracted prefix. No original host environments touched."""
import json,os,pathlib,struct,subprocess,sys
root=pathlib.Path(sys.argv[1]).resolve()
config={'python':{'platform':'runtime/ovon/bin/python','navigation':'runtime/internnav/bin/python','recognition':'runtime/internnav/bin/python'},'data_root':'data'}
(root/'runtime.json').write_text(json.dumps(config,indent=2))
old_prefixes={'ovon':['/home/abc/anaconda3/envs/ovon','/home/abc/miniconda3/envs/ovon'],
              'internnav':['/home/abc/anaconda3/envs/internnav','/home/abc/miniconda3/envs/internnav']}
for name,old in old_prefixes.items():
    prefix=root/'runtime'/name
    # Fix pip-installed entrypoint shebangs from the original moved environment.
    for path in (prefix/'bin').iterdir():
        if not path.is_file() or path.is_symlink():continue
        data=path.read_bytes()
        if not data.startswith(b'#!') or len(data)>2000000:continue
        for x in old:data=data.replace(x.encode(),str(prefix).encode())
        path.write_bytes(data)
    for path in prefix.glob('lib/python*/site-packages/*.pth'):
        text=path.read_text(errors='replace')
        if '/home/abc/' in text:
            path.write_text('\n'.join(x for x in text.splitlines() if '/home/abc/' not in x)+'\n')
libs=[root/'vendor/ORB_SLAM3/lib',root/'vendor/ORB_SLAM3/Thirdparty/DBoW2/lib',root/'vendor/ORB_SLAM3/Thirdparty/g2o/lib',root/'vendor/pangolin-install/lib']
files=list((root/'vendor').rglob('*'))+[root/'localization/build/orb_ipc']
for p in files:
    if p.is_symlink() or not p.is_file():continue
    with p.open('rb') as f:header=f.read(20)
    if header[:4]!=b'\x7fELF' or int.from_bytes(header[16:18],'little') not in (2,3):continue
    paths=['$ORIGIN']+['$ORIGIN/'+os.path.relpath(x,p.parent) for x in libs]
    subprocess.run(['patchelf','--set-rpath',':'.join(paths),str(p)],check=True)
# Private caches and model output directories, never a copy of the source HOME.
for path in ['logs','runs','state/cache','state/huggingface','vendor/InternNav/test_data']:(root/path).mkdir(parents=True,exist_ok=True)
print('Relocated environment entrypoints and all project ELF runpaths:',root)
