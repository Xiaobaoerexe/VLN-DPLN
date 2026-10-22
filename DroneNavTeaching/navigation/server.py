"""Actual local, pretrained InternVLA-N1 DualVLN inference service."""
from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "InternNav"
os.environ.setdefault("DUAL_TRACE", "0")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.chdir(VENDOR)
sys.path.insert(0, str(VENDOR))
import numpy as np
import torch
from flask import Flask, jsonify, request
from internnav.agent.internvla_n1_agent_realworld import InternVLAN1AsyncAgent

app = Flask(__name__)
lock = threading.Lock()
agent = None
instruction = ""
target = ""
step_idx = 0
load_error = None


def trajectory_action(trajectory):
    """Quantize the pretrained System1 trajectory to a 0.25 m / 10 deg action.

    System1 points use x-forward, y-left. Replan each action, so no scene map,
    object annotations, or hidden target positions enter this controller.
    """
    points = np.asarray(trajectory, dtype=float)
    if points.ndim != 2 or len(points) == 0 or not np.isfinite(points).all():
        raise ValueError("Invalid System1 trajectory")
    usable = points[np.linalg.norm(points[:, :2], axis=1) >= 0.20]
    point = usable[min(3, len(usable) - 1)] if len(usable) else points[-1]
    angle = math.atan2(float(point[1]), float(point[0]))
    if angle > math.radians(7):
        return "LEFT", point.tolist()
    if angle < -math.radians(7):
        return "RIGHT", point.tolist()
    # A tiny trajectory is not semantic task completion: only model STOP ends it.
    return "FORWARD", point.tolist()


@app.get("/health")
def health():
    return jsonify(ready=agent is not None, error=load_error,
                   model="InternVLA-N1-DualVLN", local_weights=True,
                   external_api=False, target=target, steps=step_idx)


@app.post("/reset")
def reset():
    global instruction, target, step_idx
    with lock:
        data = request.get_json(force=True)
        target = str(data["target"])
        instruction = str(data.get("instruction") or f"Find the {target} and navigate close to it, then stop.")
        agent.reset()
        agent.last_s2_idx = -100  # upstream reset omits this counter
        agent.save_dir = str(ROOT / "logs" / "policy_frames" / str(time.time_ns()))
        Path(agent.save_dir).mkdir(parents=True, exist_ok=True)
        step_idx = 0
        return jsonify(target=target, instruction=instruction, model="InternVLA-N1-DualVLN")


@app.post("/act")
def act():
    global step_idx
    with lock:
        begin = time.perf_counter()
        with np.load(io.BytesIO(request.get_data()), allow_pickle=False) as payload:
            rgb, depth, pose = payload["rgb"], payload["depth"], payload["pose"]
            intrinsic = payload["intrinsic"]
        if not intrinsic.size:
            height, width = depth.shape[:2]
            focal = width / (2 * math.tan(math.radians(45)))
            intrinsic = np.array([[focal, 0, width/2, 0], [0, focal, height/2, 0],
                                  [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
        output = agent.step(rgb, depth, pose, instruction, intrinsic=intrinsic)
        # The fixed-height drone has no LOOK_DOWN primitive. The official agent
        # accepts a second view after its down-arrow; here explicitly reuse the
        # same forward view and record that adaptation.
        look_down_reused = False
        if output.output_action == [5]:
            output = agent.step(rgb, depth, pose, instruction, intrinsic=intrinsic, look_down=True)
            look_down_reused = True
        result = {"model": "InternVLA-N1-DualVLN", "raw_output": agent.llm_output,
                  "step": step_idx, "look_down_reused_forward_view": look_down_reused}
        if output.output_action is not None:
            codes = list(output.output_action)
            if not codes:
                raise ValueError(f"Model returned unparseable action: {agent.llm_output!r}")
            code = int(codes[0])
            if code == 5:
                # Repeated look-down is interpreted as a right inspection turn.
                action = "RIGHT"
                result["adaptation"] = "repeated_LOOK_DOWN_to_RIGHT"
            else:
                action = {0: "STOP", 1: "FORWARD", 2: "LEFT", 3: "RIGHT"}[code]
            result.update(branch="System2_discrete", raw_actions=codes)
        else:
            action, waypoint = trajectory_action(output.output_trajectory)
            result.update(branch="System1_trajectory", local_waypoint=waypoint,
                          pixel_goal=output.output_pixel)
        result["action"] = action
        result["inference_ms"] = (time.perf_counter() - begin) * 1000
        result["policy_hz"] = 1000 / result["inference_ms"]
        step_idx += 1
        with (ROOT / "logs" / "policy_decisions.jsonl").open("a") as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
        return jsonify(result)


def main():
    global agent, load_error
    torch.set_num_threads(4)
    args = SimpleNamespace(device="cuda:0", model_path=str(VENDOR / "checkpoints" / "InternVLA-N1-DualVLN"),
                           resize_w=384, resize_h=384, num_history=4, plan_step_gap=4)
    start = time.perf_counter()
    agent = InternVLAN1AsyncAgent(args)
    agent.reset()
    print(f"DRONENAV_POLICY_READY load_s={time.perf_counter()-start:.2f}", flush=True)
    app.run(host="127.0.0.1", port=int(os.getenv("DRONENAV_POLICY_PORT", "5811")), threaded=True)


if __name__ == "__main__":
    main()
