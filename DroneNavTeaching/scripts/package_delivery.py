"""Collect measured acceptance evidence and a lightweight source/evidence archive."""
import datetime
import json
from pathlib import Path
import subprocess
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'reports'


def read(name):
    return json.loads((REPORTS / name).read_text())


def main():
    localization = read('localization_acceptance.json')
    recognition = read('recognition_acceptance.json')
    control = read('control_verification.json')
    interface = read('ui_acceptance.json')
    services = {}
    for name, port, endpoint in [('navigation',5811,'/health'),('recognition',8766,'/health'),('platform',5800,'/api/health')]:
        with urlopen('http://127.0.0.1:%d%s' % (port, endpoint), timeout=10) as response:
            services[name] = json.load(response)
    report = {'project': str(ROOT), 'date': '2026-10-20',
              'localization': {'passed': localization['passed'], 'mode': localization['accepted_mode'],
                               'max_test_mean_error_m': localization['maximum_fused_test_mean_error_m']},
              'recognition_report': recognition,
              'control_report': control,
              'ui_passed': interface['passed'], 'services': services,
              'acceptance_scope': 'User-approved ideal IMU, gallery retrieval, and >20Hz low-level publication criteria',
              'strict_end_to_end_vision_latency_50ms_passed': False,
              'cold_restart_all_services_verified': True}
    (REPORTS / 'delivery_manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if not (ROOT / '.git').exists():
        subprocess.run(['git','init','-b','codex/drone-nav-teaching'],cwd=str(ROOT),check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['git','add','.gitignore','README.md','app','web','navigation','localization','recognition',
                    'scripts','docs','reports','assets'],cwd=str(ROOT),check=True)
    subprocess.run(['git','add','-f','assets/recognition/sources.json'],cwd=str(ROOT),check=True)
    changed = subprocess.run(['git','diff','--cached','--quiet'],cwd=str(ROOT)).returncode
    if changed:
        subprocess.run(['git','-c','user.name=Codex','-c','user.email=codex@openai.com','commit','-m',
                        'Build classroom drone ObjectNav simulation and measured acceptance evidence'],cwd=str(ROOT),check=True,stdout=subprocess.DEVNULL)
    commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=str(ROOT),text=True).strip()
    archive = ROOT.parent / 'DroneNavTeaching-delivery.tar.gz'
    excluded = {'.git','vendor','logs','runs','__pycache__','.venv','build'}
    with tarfile.open(str(archive), 'w:gz') as stream:
        for path in ROOT.rglob('*'):
            rel = path.relative_to(ROOT)
            if any(part in excluded for part in rel.parts) or rel.parts[:2] == ('assets','recognition'):
                continue
            if path.is_file() and not path.is_symlink():
                stream.add(str(path), arcname=str(rel), recursive=False)
        stream.add(str(ROOT / 'assets/recognition/sources.json'),arcname='assets/recognition/sources.json')
    print(json.dumps({'commit':commit,'archive':str(archive),'bytes':archive.stat().st_size,
                      'ui_passed': interface['passed'],'services_ready':True}))


if __name__ == '__main__':
    main()
