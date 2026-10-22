"""Standalone OVON closed-loop smoke/soak check (no ground-truth navigation)."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import sys
import time

import habitat_sim
import numpy as np
import quaternion

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from navigation import NavigationPolicy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--scene-index", type=int, default=0)
    parser.add_argument("--episode-index", type=int, default=0)
    args = parser.parse_args()
    data = Path("/home/abc/Documents/VLN/ovon/data")
    files = sorted(p for p in (data / "datasets/ovon/hm3d/val_unseen/content").glob("*.json.gz") if not p.name.startswith("._"))
    episode_file = files[args.scene_index]
    with gzip.open(episode_file, "rt") as stream:
        dataset = json.load(stream)
    ep = dataset["episodes"][args.episode_index]
    backend = habitat_sim.SimulatorConfiguration()
    backend.scene_id = str(data / "scene_datasets" / ep["scene_id"].replace(".basis.glb", ".glb"))
    config_path = ep.get("scene_dataset_config", "data/scene_datasets/hm3d/hm3d_annotated_basis.scene_dataset_config.json")
    backend.scene_dataset_config_file = str(Path("/home/abc/Documents/VLN/ovon") / config_path)
    backend.enable_physics = False
    backend.gpu_device_id = 0
    sensors = []
    for name, kind in [("rgb", habitat_sim.SensorType.COLOR), ("depth", habitat_sim.SensorType.DEPTH)]:
        spec = habitat_sim.CameraSensorSpec()
        spec.uuid, spec.sensor_type = name, kind
        spec.resolution = [480, 640]
        spec.position = [0, 1.4, 0]
        spec.hfov = 90
        sensors.append(spec)
    agent_cfg = habitat_sim.agent.AgentConfiguration()
    agent_cfg.radius = 0.1
    agent_cfg.height = 1.4
    agent_cfg.sensor_specifications = sensors
    agent_cfg.action_space = {
        "move_forward": habitat_sim.agent.ActionSpec("move_forward", habitat_sim.agent.ActuationSpec(amount=0.25)),
        "turn_left": habitat_sim.agent.ActionSpec("turn_left", habitat_sim.agent.ActuationSpec(amount=10)),
        "turn_right": habitat_sim.agent.ActionSpec("turn_right", habitat_sim.agent.ActuationSpec(amount=10)),
    }
    sim = habitat_sim.Simulator(habitat_sim.Configuration(backend, [agent_cfg]))
    agent = sim.initialize_agent(0)
    state = agent.get_state()
    state.position = sim.pathfinder.snap_point(np.asarray(ep["start_position"], dtype=np.float32))
    r = ep["start_rotation"]
    state.rotation = np.quaternion(r[3], r[0], r[1], r[2])
    agent.set_state(state)
    policy = NavigationPolicy()
    policy.reset(ep["object_category"])
    rows, latencies, distance = [], [], 0.0
    obs = sim.get_sensor_observations()
    begin = time.perf_counter()
    name_map = {"FORWARD": "move_forward", "LEFT": "turn_left", "RIGHT": "turn_right"}
    for index in range(min(args.max_steps, 500)):
        action = policy.act(obs["rgb"][..., :3], obs["depth"])
        before = np.asarray(agent.get_state().position, dtype=float).copy()
        if action != "STOP":
            obs = sim.step(name_map[action])
        after = np.asarray(agent.get_state().position, dtype=float)
        displacement = float(np.linalg.norm(after-before))
        distance += displacement
        if displacement > 0.251:
            raise AssertionError(f"Motion primitive exceeded 0.25 m: {displacement}")
        row = dict(policy.last_info)
        row.update(index=index, position=after.tolist(), displacement_m=displacement)
        rows.append(row)
        latencies.append(row["inference_ms"])
        if index % 10 == 0 or action == "STOP":
            print(json.dumps({"step": index, "action": action, "raw": row["raw_output"],
                              "branch": row["branch"], "inference_ms": row["inference_ms"],
                              "displacement_m": displacement}, ensure_ascii=False), flush=True)
        if action == "STOP":
            break
    result = {"model": "InternVLA-N1-DualVLN", "scene": episode_file.name, "episode_id": ep["episode_id"],
              "target": ep["object_category"], "steps": len(rows), "budget": min(args.max_steps, 500),
              "stop_received": rows[-1]["action"] == "STOP", "travelled_m": distance,
              "camera_height_m": 1.4, "translation_step_m": 0.25, "turn_deg": 10,
              "elapsed_s": time.perf_counter()-begin, "mean_policy_ms": float(np.mean(latencies)),
              "action_counts": dict(Counter(r["action"] for r in rows)), "rows": rows,
              "uses_ground_truth_goal_for_actions": False, "external_api": False,
              "passed": len(rows) > 0 and len(rows) <= 500}
    output = ROOT / "logs" / f"navigation_check_{args.scene_index}_{args.episode_index}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print("RESULT " + json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False), flush=True)
    sim.close()


if __name__ == "__main__":
    main()
