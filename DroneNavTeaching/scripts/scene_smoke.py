"""Serial, real-render smoke check of all 36 unfiltered OVON scenes."""
from collections import Counter
import json
import math
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np
import quaternion
from app.simulator import DroneSimulator, EpisodeCatalog


def main():
    output = ROOT / 'reports/scene_smoke.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    frame_dir = ROOT / 'reports/scene_smoke_frames'
    frame_dir.mkdir(exist_ok=True)
    catalog = EpisodeCatalog()
    report = {'scene_count': len(catalog.files), 'selection': 'every scene, episode_index=0, no exclusions',
              'no_navigation_model_calls': True, 'scenes': [], 'started': time.strftime('%Y-%m-%d %H:%M:%S')}
    began = time.perf_counter()
    for index in range(len(catalog.files)):
        start = time.perf_counter()
        simulator = None
        ep = catalog.choose(scene_index=index, episode_index=0)
        row = {'scene_index': index, 'episode_id': str(ep['episode_id']),
               'scene_id': ep['scene_id'], 'target': ep['object_category'], 'passed': False,
               'original_start_position': ep['start_position'], 'original_start_rotation': ep['start_rotation']}
        try:
            # Retain a handle even if __init__ raises after creating the GL context.
            simulator = DroneSimulator.__new__(DroneSimulator)
            simulator.__init__(ep, seed=index, render_overview=True)
            row.update(simulator.metadata())
            rgb, depth, overview = simulator.observe()
            position, rotation = simulator.camera_pose()
            row.update(rgb_shape=list(rgb.shape), rgb_std=float(rgb.std()),
                       depth_shape=list(depth.shape), finite_depth_ratio=float(np.isfinite(depth).mean()),
                       positive_depth_ratio=float((depth > 0).mean()), overview_shape=list(overview.shape),
                       initial_camera_position=position.tolist(),
                       measured_camera_height_m=float(position[1] - simulator.floor_y))
            assert rgb.shape == (480, 640, 3) and rgb.std() > 1, 'blank or malformed RGB'
            assert depth.shape == (480, 640) and np.isfinite(depth).all(), 'malformed depth'
            assert (depth > 0).mean() > .5, 'too few valid depth samples'
            assert overview.shape == (240, 320, 3), 'overview missing'
            assert abs(row['measured_camera_height_m'] - 1.4) < 1e-4, 'camera height mismatch'
            cv2.imwrite(str(frame_dir / ('%02d_%s.jpg' % (index, row['scene']))), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            action_rows = []
            row['actions'] = action_rows
            for action in ['FORWARD', 'LEFT', 'RIGHT']:
                before = simulator.agent.get_state()
                before_position = np.asarray(before.position).copy()
                before_rotation = before.rotation
                substeps = list(simulator.advance(action))
                after = simulator.agent.get_state()
                displacement = float(np.linalg.norm(np.asarray(after.position) - before_position))
                relative = after.rotation * before_rotation.conjugate()
                relative = relative / abs(relative)
                angle = 2 * math.acos(min(1., abs(float(relative.w))))
                action_rows.append({'action': action, 'substeps': len(substeps), 'displacement_m': displacement,
                                    'yaw_change_deg': math.degrees(angle), 'steps': simulator.steps})
                assert len(substeps) == 8, 'intermediate frame count mismatch'
                assert abs(float(after.position[1]) - simulator.floor_y) < 1e-5, 'altitude changed'
                if action == 'FORWARD':
                    assert displacement <= .25001, 'translation exceeds 0.25 m'
                    assert angle < 1e-4, 'forward changed yaw'
                else:
                    assert displacement < 1e-5, 'turn produced translation'
                    assert abs(math.degrees(angle) - 10) < .001, 'turn not 10 degrees'
            row.update(actions=action_rows, passed=True)
        except Exception as exc:
            row.update(error=type(exc).__name__ + ': ' + str(exc), traceback=traceback.format_exc())
        finally:
            if simulator is not None and hasattr(simulator, 'sim'):
                try:
                    simulator.close()
                except Exception as exc:
                    row['close_error'] = str(exc)
            row['elapsed_s'] = time.perf_counter() - start
            report['scenes'].append(row)
            report['passed'] = sum(x['passed'] for x in report['scenes'])
            report['failed'] = len(report['scenes']) - report['passed']
            report['complete'] = len(report['scenes']) == len(catalog.files)
            report['elapsed_s'] = time.perf_counter() - began
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps({k: row.get(k) for k in ['scene_index', 'scene', 'episode_id', 'target', 'passed',
                                                       'start_snap_m', 'navigable_area_m2', 'asset_loaded', 'error', 'elapsed_s']},
                             ensure_ascii=False), flush=True)
    print('RESULT ' + json.dumps({k: v for k, v in report.items() if k != 'scenes'}), flush=True)
    return 0 if report['failed'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
