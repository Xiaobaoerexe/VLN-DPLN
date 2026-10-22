"""Independent ORB-SLAM3 localization acceptance; GT is evaluation-only after reset.
Scripted navmesh paths stress translation/turning. They are not a navigation model.
"""
import argparse, gzip, json, math, os, sys, time
from pathlib import Path
import numpy as np
import quaternion
import habitat_sim
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from localization import FusedLocalizer
from localization.evaluate_fused import gyro_between
from localization.orb_slam3 import quaternion_matrix
from localization.ideal_imu import GRAVITY

def quat(yaw):return np.quaternion(math.cos(yaw/2),0,math.sin(yaw/2),0)
def arrq(q):return [float(q.w),float(q.x),float(q.y),float(q.z)]
def main():
 p=argparse.ArgumentParser();p.add_argument('--scene-index',type=int,default=0);p.add_argument('--distance',type=float,default=20);p.add_argument('--yaw-step',type=float,default=1.25);p.add_argument('--translation-step',type=float,default=.03125);p.add_argument('--label',default='smooth');p.add_argument('--blackout',type=int,default=0);p.add_argument('--max-frames',type=int,default=5000);args=p.parse_args()
 data=Path('/home/abc/Documents/VLN/ovon/data');files=sorted(f for f in (data/'datasets/ovon/hm3d/val_unseen/content').glob('*.json.gz') if not f.name.startswith('._'))
 dataset=json.load(gzip.open(files[args.scene_index],'rt'));ep=dataset['episodes'][0]
 b=habitat_sim.SimulatorConfiguration();b.scene_id=str(data/'scene_datasets'/ep['scene_id'].replace('.basis.glb','.glb'));b.scene_dataset_config_file=str(data/'scene_datasets/hm3d/hm3d_annotated_basis.scene_dataset_config.json');b.enable_physics=False;b.gpu_device_id=0
 specs=[]
 for name,kind in [('rgb',habitat_sim.SensorType.COLOR),('depth',habitat_sim.SensorType.DEPTH)]:
  s=habitat_sim.CameraSensorSpec();s.uuid=name;s.sensor_type=kind;s.resolution=[480,640];s.position=[0,1.4,0];s.hfov=90;specs.append(s)
 ac=habitat_sim.agent.AgentConfiguration();ac.radius=.1;ac.height=1.4;ac.sensor_specifications=specs
 sim=habitat_sim.Simulator(habitat_sim.Configuration(b,[ac]));sim.pathfinder.seed(20261020+args.scene_index)
 agent=sim.initialize_agent(0);state=agent.get_state();state.position=sim.pathfinder.snap_point(np.asarray(ep['start_position'],np.float32));r=ep['start_rotation'];state.rotation=np.quaternion(r[3],r[0],r[1],r[2]);agent.set_state(state)
 sensor=agent.get_state().sensor_states['rgb'];localizer=FusedLocalizer();localizer.reset(sensor.position,arrq(sensor.rotation))
 last_sensor_position=np.asarray(sensor.position,dtype=float).copy();last_sensor_rotation=sensor.rotation;last_sensor_velocity=np.zeros(3)
 rows=[];dist=0.;start=time.time();lastpos=np.asarray(state.position).copy();counter=0
 def frame():
  nonlocal counter,lastpos,dist,last_sensor_position,last_sensor_rotation,last_sensor_velocity
  obs=sim.get_sensor_observations();truth=agent.get_state().sensor_states['rgb'];rgb=obs['rgb'][:,:,:3]
  if args.blackout and args.blackout<=counter<args.blackout+15:rgb=np.zeros_like(rgb)
  imu=None
  if counter:
   velocity=(np.asarray(truth.position,dtype=float)-last_sensor_position)*30
   acceleration=(velocity-last_sensor_velocity)*30
   R=quaternion_matrix(arrq(truth.rotation))
   imu=dict(gyro_body_rad_s=gyro_between(last_sensor_rotation,truth.rotation,1/30.).tolist(),specific_force_body_m_s2=(R.T@(acceleration-GRAVITY)).tolist(),dt=1/30.)
   last_sensor_velocity=velocity
  last_sensor_position=np.asarray(truth.position,dtype=float).copy();last_sensor_rotation=truth.rotation
  result=localizer.track(rgb,obs['depth'],counter/30.,imu=imu);counter+=1
  gt=np.asarray(truth.position);estimate=result['position'];error=float(np.linalg.norm(np.asarray(estimate)-gt)) if estimate is not None else None
  body=np.asarray(agent.get_state().position);dist+=float(np.linalg.norm(body-lastpos));lastpos=body.copy()
  row=dict(frame=counter,gt_position=gt.tolist(),estimated_position=estimate,error_m=error,tracking=result['tracking'],visual_tracking=result['visual_tracking'],map_id=result['map_id'],map_point_count=result['map_point_count'],latency_ms=result['latency_ms']);rows.append(row)
  if counter%100==0:print(json.dumps(dict(frame=counter,error_m=error,tracking=result['tracking'],travelled_m=dist,map_points=result['map_point_count'],elapsed_s=time.time()-start)),flush=True)
 for _ in range(5):frame()
 attempts=0
 while dist<args.distance and counter<args.max_frames:
  current=agent.get_state();point=np.asarray(current.position);path=habitat_sim.ShortestPath();path.requested_start=point;path.requested_end=sim.pathfinder.get_random_navigable_point();attempts+=1
  if attempts>200:break
  if not sim.pathfinder.find_path(path) or path.geodesic_distance<3 or abs(path.requested_end[1]-point[1])>.2:continue
  for target in list(path.points)[1:]:
   target=np.asarray(target);v=target-point;v[1]=0;length=float(np.linalg.norm(v))
   if length<.01:continue
   desired=math.atan2(-float(v[0]),-float(v[2]));fwd=quaternion.rotate_vectors(current.rotation,[0,0,-1]);yaw=math.atan2(-float(fwd[0]),-float(fwd[2]));delta=(desired-yaw+math.pi)%(2*math.pi)-math.pi
   n=max(1,math.ceil(abs(math.degrees(delta))/args.yaw_step))
   for j in range(1,n+1):
    current.rotation=quat(yaw+delta*j/n);agent.set_state(current);frame()
   n=max(1,math.ceil(length/args.translation_step));origin=point.copy()
   for j in range(1,n+1):
    # Sample the continuous navmesh segment; GT is not sent to the localizer.
    current.position=origin+(target-origin)*j/n;agent.set_state(current);frame()
    if dist>=args.distance or counter>=args.max_frames:break
   point=np.asarray(current.position).copy()
   if dist>=args.distance or counter>=args.max_frames:break
 errors=[r['error_m'] for r in rows if r['error_m'] is not None];gt=np.array([r['gt_position'] for r in rows]);span=np.ptp(gt[:,[0,2]],axis=0)
 result=dict(algorithm='Ideal noiseless IMU strapdown + ORB-SLAM3 RGB-D mapping',scene=ep['scene_id'],scene_index=args.scene_index,label=args.label,initial_ground_truth_alignment='one rigid transform at first tracked frame',ground_truth_used_after_reset=False,artificial_motion_noise=False,imu_used=True,imu_noise=False,imu_bias=False,imu_contract='semi-implicit end-body-frame force',camera_height_m=1.4,resolution=[640,480],hfov_deg=90,translation_substep_m=args.translation_step,yaw_substep_deg=args.yaw_step,nominal_input_fps=30,navigable_area_m2=float(sim.pathfinder.navigable_area),trajectory_bbox_xz_m=span.tolist(),trajectory_bbox_area_m2=float(np.prod(span)),travelled_m=dist,frames=len(rows),tracked_frames=len(errors),coverage=len(errors)/len(rows),mean_error_m=float(np.mean(errors)) if errors else None,rmse_m=float(np.sqrt(np.mean(np.square(errors)))) if errors else None,max_error_m=float(np.max(errors)) if errors else None,p95_error_m=float(np.percentile(errors,95)) if errors else None,mean_tracking_latency_ms=float(np.mean([r['latency_ms'] for r in rows])),elapsed_s=time.time()-start,all_frames_tracked=len(errors)==len(rows),accuracy_pass=bool(errors and np.mean(errors)<=.1),coverage_pass=len(errors)==len(rows),space_pass=sim.pathfinder.navigable_area>=50)
 result['passed']=result['accuracy_pass'] and result['coverage_pass'] and result['space_pass'];result['rows']=rows
 output=ROOT/'reports'/('localization_fused_'+args.label+'_'+str(args.scene_index)+'.json');output.write_text(json.dumps(result,indent=2));print('RESULT '+json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True);localizer.close();sim.close()
if __name__=='__main__':main()
