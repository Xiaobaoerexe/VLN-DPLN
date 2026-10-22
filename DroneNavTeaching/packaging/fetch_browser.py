"""Online builder helper: parallel ranged official Mozilla download, no installer use."""
import concurrent.futures,hashlib,json,os,pathlib,re,subprocess,sys,time,urllib.request
out=pathlib.Path(sys.argv[1]);out.parent.mkdir(parents=True,exist_ok=True)
url='https://archive.mozilla.org/pub/firefox/releases/153.5.1esr/linux-x86_64/zh-CN/firefox-153.5.1esr.tar.xz'
total=89431168
parts=out.parent.parent/'preparation/browser-chunks';parts.mkdir(parents=True,exist_ok=True)
count=(total+1048575)//1048576
def fetch(i):
    begin=i*1048576;end=min(total,(i+1)*1048576)-1;p=parts/('%03d'%i)
    if p.exists() and p.stat().st_size==end-begin+1:return p
    for trial in range(4):
        try:
            headers=parts/('%03d.headers'%i)
            subprocess.run(['curl','-fLsS','--range','%d-%d'%(begin,end),'--connect-timeout','10',
                            '--max-time','180','--retry','2','-D',str(headers),'-o',str(p),url+'?offline_chunk='+str(i)],check=True)
            text=headers.read_text(errors='replace').lower()
            assert 'content-range: bytes %d-%d/%d'%(begin,end,total) in text,'incorrect Content-Range'
            assert p.stat().st_size==end-begin+1
            print('part',i,'complete',flush=True);return p
        except Exception:
            if trial==3:raise
            time.sleep(2)
with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:files=list(pool.map(fetch,range(count)))
with out.with_suffix('.partial').open('wb') as f:
    for p in files:
        with p.open('rb') as inp:
            while True:
                data=inp.read(1024*1024)
                if not data:break
                f.write(data)
os.replace(out.with_suffix('.partial'),out)
subprocess.run(['xz','-t',str(out)],check=True)
checksum=parts/'SHA512SUMS'
subprocess.run(['curl','-fLsS','--max-time','120','-o',str(checksum),'https://archive.mozilla.org/pub/firefox/releases/153.5.1esr/SHA512SUMS'],check=True)
line=next(x for x in checksum.read_text().splitlines() if x.endswith('linux-x86_64/zh-CN/firefox-153.5.1esr.tar.xz'))
actual=hashlib.sha512(out.read_bytes()).hexdigest();assert actual==line.split()[0]
(out.parent.parent/'audit/browser-source.json').write_text(json.dumps({'source':url,'bytes':out.stat().st_size,'version':'153.5.1esr','xz_validated':True,'official_sha512_verified':True,'sha512':actual},indent=2))
print('Firefox complete:',out,flush=True)
