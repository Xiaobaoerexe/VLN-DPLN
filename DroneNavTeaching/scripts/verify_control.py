"""V2 control check: real 50 Hz callbacks plus the asynchronous runtime regression.

No running UI/model is contacted. Use the installed OVON Python interpreter.
"""
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
from app.control import CommandPublisher

def real_publication():
    publisher = CommandPublisher(50)
    callbacks, decisions = [], {}
    publisher.callback = lambda record: callbacks.append(dict(record))
    try:
        for index in range(40):
            command = 'FORWARD_%02d' % index
            decisions[command] = time.perf_counter()
            publisher.set(command)
            # A new perception completion timestamp is distinct from model decision.
            publisher.recognition_done(time.perf_counter() - .12)
            time.sleep(.037)
        time.sleep(.12)
        before = len(callbacks)
        paused_at = time.perf_counter()
        publisher.pause()
        time.sleep(.18)
        after = len(callbacks)
        first = {}
        for callback in callbacks:
            first.setdefault(callback['command'], callback['monotonic'])
        dispatch = [(first[c] - timestamp) * 1000 for c, timestamp in decisions.items() if c in first]
        times = [x['monotonic'] for x in callbacks]
        measured = (len(times) - 1) / (times[-1] - times[0])
        metrics = publisher.metrics()
        result = {'actual_callback_count': len(callbacks), 'measured_callback_hz': measured,
                  'mean_set_to_first_callback_ms': float(np.mean(dispatch)),
                  'max_set_to_first_callback_ms': float(np.max(dispatch)), 'decisions_observed': len(dispatch),
                  'callbacks_after_pause': after - before, 'pause_stopped_publication': after == before,
                  'last_callback_relative_to_pause_ms': (times[-1] - paused_at) * 1000,
                  'publisher_metrics': metrics,
                  'passed': measured > 20 and after == before and float(np.mean(dispatch)) <= 50}
        assert result['passed'], result
        return result
    finally:
        publisher.pause()
        publisher.shutdown.set()


def main():
    publisher=real_publication()
    from scripts import verify_realtime_runtime
    code=verify_realtime_runtime.main()
    asynchronous=json.loads((ROOT/'reports/realtime_runtime_regression.json').read_text())
    report={'publisher':publisher,'step_budget':asynchronous['budget_500'],
            'asynchronous_runtime':asynchronous,'passed':publisher['passed'] and code==0,
            'scope':'actual low-level held-command callbacks and V2 asynchronous state-machine regression; not semantic VLM end-to-end speed'}
    (ROOT/'reports/control_verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2),flush=True)
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
