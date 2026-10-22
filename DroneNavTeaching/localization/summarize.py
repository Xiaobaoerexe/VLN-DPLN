"""Aggregate localization evidence without hiding RGB-D-only failures."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
expected=['localization_fused_random_0.json','localization_fused_spin_1.json','localization_fused_collision_0.json','localization_fused_smooth_1.json']
reports=[];missing=[]
for name in expected:
    p=ROOT/'reports'/name
    if not p.exists():missing.append(name);continue
    result=json.loads(p.read_text());result.pop('rows',None);result['evidence_file']='reports/'+name;reports.append(result)
visual=[]
for name in ['localization_smooth_0.json','localization_smooth_1.json','localization_abrupt_0.json']:
    result=json.loads((ROOT/'reports'/name).read_text());result.pop('rows',None);result['evidence_file']='reports/'+name;visual.append(result)
wide=[r for r in reports if r.get('trajectory_bbox_area_m2',0)>=50]
passed=not missing and all(r['passed'] for r in reports) and bool(wide)
result=dict(criterion='1.1',passed=passed,threshold_mean_error_m=.1,min_space_area_m2=50,
 accepted_mode='Ideal noiseless IMU strapdown + real ORB-SLAM3 RGB-D mapping',
 initial_absolute_pose_only=True,absolute_pose_input_after_reset=False,
 imu_inputs=['gyro_body_rad_s','specific_force_body_m_s2','dt'],
 imu_noise=False,imu_bias=False,initial_velocity=[0,0,0],visual_kalman_gain=0.,
 imu_contract='semi-implicit discrete integration with end-body-frame specific force',
 tested_controls=['500 random OVON actions','pure yaw rotation','forward collision','stationary intervals','45-frame camera blackout','25m trajectory spanning >50m2'],
 missing_reports=missing,fused_tests=reports,visual_only_tests=visual,
 maximum_fused_test_mean_error_m=max((r['mean_error_m'] for r in reports),default=None),
 worst_fused_test_frame_error_m=max((r['max_error_m'] for r in reports),default=None),
 evidence_scope='Measured simulation results under the stated ideal IMU contract; no claim of general real-world inertial performance or arbitrary-scene visual-only guarantee.')
(ROOT/'reports/localization_acceptance.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k not in ['fused_tests','visual_only_tests']},indent=2))
