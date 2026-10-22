"""Local InternVLA-N1 DualVLN adapter for the teaching simulator.

The actual network lives in a separate internnav conda process because the
Habitat simulator uses a different Python environment. No external API is used.
"""
from __future__ import annotations

import io
import os
import time

import numpy as np
import requests


class NavigationPolicy:
    def __init__(self, url=None, timeout=180):
        self.url = (url or os.getenv("DRONENAV_POLICY_URL", "http://127.0.0.1:5811")).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.last_info = {}
        self.target = None

    def health(self):
        response = self.session.get(self.url + "/health", timeout=5)
        response.raise_for_status()
        return response.json()

    def reset(self, target, instruction=None, **kwargs):
        self.target = str(target)
        response = self.session.post(self.url + "/reset", json={
            "target": self.target,
            "instruction": instruction or f"Find the {self.target} and navigate close to it, then stop.",
        }, timeout=self.timeout)
        response.raise_for_status()
        self.last_info = response.json()
        return self.last_info

    def act(self, rgb, depth, pose=None, intrinsic=None, **kwargs):
        if self.target is None:
            raise RuntimeError("Call reset(target) before act().")
        stream = io.BytesIO()
        np.savez(stream, rgb=np.asarray(rgb, dtype=np.uint8)[..., :3],
                 depth=np.asarray(depth, dtype=np.float32),
                 pose=np.eye(4, dtype=np.float32) if pose is None else np.asarray(pose, dtype=np.float32),
                 intrinsic=np.asarray(intrinsic, dtype=np.float32) if intrinsic is not None else np.empty(0))
        begin = time.perf_counter()
        response = self.session.post(self.url + "/act", data=stream.getvalue(),
                                     headers={"Content-Type": "application/octet-stream"}, timeout=self.timeout)
        response.raise_for_status()
        self.last_info = response.json()
        self.last_info["roundtrip_ms"] = (time.perf_counter() - begin) * 1000
        action = self.last_info["action"]
        if action not in {"STOP", "FORWARD", "LEFT", "RIGHT"}:
            raise RuntimeError(f"Unexpected navigation action: {action!r}")
        return action

