"""Measured periodic command publication, separate from semantic planning."""
from collections import deque
import threading
import time


class CommandPublisher:
    def __init__(self, hz=50):
        self.period = 1. / hz
        self.lock = threading.Lock()
        self.active = False
        self.command = 'HOLD'
        self.latest_recognition_time = None
        self.pending_decision = None
        self.intervals = deque(maxlen=5000)
        self.dispatch_ms = deque(maxlen=5000)
        self.vision_age_ms = deque(maxlen=5000)
        self.ticks = 0
        self.callback = None
        self.shutdown = threading.Event()
        threading.Thread(target=self._run, daemon=True, name='command-publisher-%ghz' % hz).start()

    def set(self, command, active=True):
        with self.lock:
            self.command, self.active = command, active
            self.pending_decision = time.perf_counter()

    def pause(self):
        self.set('HOLD', active=False)

    def recognition_done(self, observation_time):
        with self.lock:
            self.latest_recognition_time = observation_time

    def clear_metrics(self):
        with self.lock:
            for data in [self.intervals, self.dispatch_ms, self.vision_age_ms]:
                data.clear()
            self.ticks = 0

    def _run(self):
        last = None
        deadline = time.perf_counter()
        while not self.shutdown.is_set():
            deadline += self.period
            self.shutdown.wait(max(0., deadline - time.perf_counter()))
            now = time.perf_counter()
            if now - deadline > self.period:
                deadline = now
            with self.lock:
                if not self.active:
                    last = None
                    continue
                if last is not None:
                    self.intervals.append(now - last)
                last = now
                self.ticks += 1
                if self.pending_decision is not None:
                    self.dispatch_ms.append((now - self.pending_decision) * 1000)
                    self.pending_decision = None
                if self.latest_recognition_time is not None:
                    self.vision_age_ms.append((now - self.latest_recognition_time) * 1000)
                command = self.command
            if self.callback:
                self.callback({'monotonic': now, 'command': command, 'tick': self.ticks})

    def metrics(self):
        with self.lock:
            def mean(data):
                return sum(data) / len(data) if data else None
            interval = mean(self.intervals)
            return {'nominal_hz': 1. / self.period,
                    'measured_hz': 1. / interval if interval else None,
                    'mean_publish_period_ms': interval * 1000 if interval else None,
                    'decision_to_publish_ms': mean(self.dispatch_ms),
                    'perception_age_at_publish_ms': mean(self.vision_age_ms),
                    'samples': len(self.intervals), 'ticks': self.ticks,
                    'current_command': self.command, 'active': self.active,
                    'scope': '%gHz held-command publisher; semantic inference runs independently' % (1. / self.period)}
