"""Record the root agent's actual browser QA and independently audit shutdown."""
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import manage

def main():
    lifecycle=manage._state()
    expected=[1951937,1951941,1951942,1951943]
    current=[pid for pid in expected if manage.identity(pid)]
    session=dict(source='Root agent actual browser interaction plus independent process audit',
        observed_supervisor_pid=1951937,
        observed_service_pids=dict(navigation=1951941,recognition=1951942,platform=1951943),
        actions=['start simulation','click frontend Exit'],
        frontend_exit_message='平台已退出',polling_stopped=True,mjpeg_disconnected=True,
        root_observed_cuda_compute_processes_after=[],
        independent_remaining_observed_pids=current,
        independent_supervisor_active=bool(manage._active_state()),
        independent_remaining_registered_processes=lifecycle.get('remaining_processes',[]),
        final_lifecycle_status=lifecycle.get('status'),
        observed_run_id=lifecycle.get('run_id'),
        audited_at_utc=datetime.now(timezone.utc).isoformat())
    session['passed']=not current and not session['independent_supervisor_active'] and not session['independent_remaining_registered_processes'] and session['final_lifecycle_status']=='stopped'
    (ROOT/'reports/lifecycle_root_browser_qa.json').write_text(json.dumps(session,ensure_ascii=False,indent=2))
    evidence={name:json.loads((ROOT/'reports'/file).read_text()) for name,file in dict(isolated_process_tests='lifecycle_integration.json',actual_model_api_exit='lifecycle_live.json',default_browser_launch='browser_launch.json').items()}
    evidence['actual_frontend_exit']=session
    evidence['passed']=session['passed'] and all(evidence[name]['passed'] for name in ['isolated_process_tests','actual_model_api_exit','default_browser_launch'])
    evidence['note']='Initial browser return code 4 remains in the original live evidence; the corrected desktop environment is separately verified with return code 0, Firefox HTTP traffic and a visible window.'
    (ROOT/'reports/lifecycle_acceptance.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
    print(json.dumps(dict(passed=evidence['passed'],root_browser_qa=session),ensure_ascii=False,indent=2))
    return 0 if evidence['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
