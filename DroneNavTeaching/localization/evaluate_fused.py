"""Habitat-generated ideal IMU acceptance of the fused classroom localizer.
GT is used by the simulator's IMU sensor and the independent evaluator, never as
an absolute observation after localizer.reset(). Discrete sampling is explicit.
"""
import argparse,gzip,json,math,sys,time
from pathlib import Path
import numpy as np
import quaternion
import habitat_sim
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from localization import FusedLocalizer
from localization.orb_slam3 import quaternion_matrix
from localization.ideal_imu import GRAVITY

def qarr(q):return np.array([q.w,q.x,q.y,q.z],dtype=float)
def gyro_between(old,new,dt):
    delta=old.conjugate()*new;v=qarr(delta);v/=np.linalg.norm(v)
    if v[0]<0:v=-v
    norm=np.linalg.norm(v[1:]);return (np.zeros(3) if norm<1e-12 else v[1:]/norm*(2*math.atan2(norm,v[0]))/dt)

def main():
 p=argparse.ArgumentParser();p.add_argument('--scene-index',type=int,default=0);p.add_argument('--actions',type=int,default=500);p.add_argument('--substeps',type=int,default=8);p.add_argument('--mode',choices=['random','spin','collision'],default='random');p.add_argument('--blackout-start',type=int,default=120);p.add_argument('--blackout-frames',type=int,default=45);args=p.parse_args()
 data=Path('/home/abc/Documents/VLN/ovon/data');files=sorted(f for f in (data/'datasets/ovon/hm3d/val_unseen/content').glob('*.json.gz') if not f.name.startswith('._'))
 dataset=json.load(gzip.open(files[args.scene_index],'rt'));ep=dataset['episodes'][0]
 b=habitat_sim.SimulatorConfiguration();b.scene_id=str(data/'scene_datasets'/ep['scene_id'].replace('.basis.glb','.glb'));b.enable_physics=False;b.gpu_device_id=0
 specs=[]
 for name,kind in [('rgb',habitat_sim.SensorType.COLOR),('depth',habitat_sim.SensorType.DEPTH)]:
  s=habitat_sim.CameraSensorSpec();s.uuid=name;s.sensor_type=kind;s.resolution=[480,640];s.position=[0,1.4,0];s.hfov=90;specs.append(s)
 ac=habitat_sim.agent.AgentConfiguration();ac.radius=.1;ac.height=1.4;ac.sensor_specifications=specs
 ac.action_space={'forward':habitat_sim.agent.ActionSpec('move_forward',habitat_sim.agent.ActuationSpec(amount=.25/args.substeps)),'left':habitat_sim.agent.ActionSpec('turn_left',habitat_sim.agent.ActuationSpec(amount=10/args.substeps)),'right':habitat_sim.agent.ActionSpec('turn_right',habitat_sim.agent.ActuationSpec(amount=10/args.substeps))}
 sim=habitat_sim.Simulator(habitat_sim.Configuration(b,[ac]));agent=sim.initialize_agent(0);state=agent.get_state();state.position=sim.pathfinder.snap_point(np.asarray(ep['start_position'],np.float32));r=ep['start_rotation'];state.rotation=np.quaternion(r[3],r[0],r[1],r[2]);agent.set_state(state)
 sensor=agent.get_state().sensor_states['rgb'];last_position=np.asarray(sensor.position,dtype=float).copy();last_rotation=sensor.rotation;last_velocity=np.zeros(3)
 locator=FusedLocalizer();locator.reset(last_position,qarr(last_rotation))
 obs=sim.get_sensor_observations();locator.track(obs['rgb'][:,:,:3],obs['depth'],0.)
 rows=[];rng=np.random.RandomState(20261020);start=time.time();frame=0;distance=0.;collisions=0;stops=0;counts={}
 for i in range(args.actions):
  action=('left' if args.mode=='spin' else 'forward' if args.mode=='collision' else rng.choice(['forward','left','right','static'],p=[.55,.18,.18,.09]));counts[action]=counts.get(action,0)+1
  for _ in range(args.substeps):
   obs=sim.get_sensor_observations() if action=='static' else sim.step(action)
   truth=agent.get_state().sensor_states['rgb'];position=np.asarray(truth.position,dtype=float).copy();rotation=truth.rotation;dt=1/30.
   velocity=(position-last_position)/dt;acceleration=(velocity-last_velocity)/dt;R=quaternion_matrix(qarr(rotation))
   imu=dict(gyro_body_rad_s=gyro_between(last_rotation,rotation,dt).tolist(),specific_force_body_m_s2=(R.T@(acceleration-GRAVITY)).tolist(),dt=dt)
   distance+=float(np.linalg.norm(position-last_position))
   if action=='forward' and np.linalg.norm(position-last_position)<1e-5:collisions+=1
   if action=='static':stops+=1
   last_position=position;last_velocity=velocity;last_rotation=rotation;frame+=1
   rgb=obs['rgb'][:,:,:3]
   if args.blackout_start<=frame<args.blackout_start+args.blackout_frames:rgb=np.zeros_like(rgb)
   result=locator.track(rgb,obs['depth'],frame/30.,imu=imu)
   error=float(np.linalg.norm(np.asarray(result['position'])-position))
   rows.append(dict(frame=frame,action=action,error_m=error,gt_position=position.tolist(),estimated_position=result['position'],tracking=result['tracking'],visual_tracking=result['visual_tracking'],visual_innovation_m=result['visual_innovation_m'],map_id=result['map_id'],map_point_count=result['map_point_count'],latency_ms=result['latency_ms']))
   if frame%250==0:print(json.dumps(dict(frame=frame,actions=i+1,error_m=error,visual_tracking=result['visual_tracking'],travelled_m=distance,map_points=result['map_point_count'],elapsed_s=time.time()-start)),flush=True)
 errors=np.array([x['error_m'] for x in rows]);report=dict(algorithm='Ideal noiseless IMU strapdown + ORB-SLAM3 RGB-D mapping',scene=ep['scene_id'],scene_index=args.scene_index,mode=args.mode,actions=args.actions,substeps=args.substeps,frames=frame,camera_height_m=1.4,navigable_area_m2=float(sim.pathfinder.navigable_area),motion_noise=False,imu_noise=False,imu_bias=False,initial_velocity_m_s=[0,0,0],initial_ground_truth_pose_only=True,absolute_ground_truth_observations_after_reset=0,imu_measurements=['gyro_body_rad_s','specific_force_body_m_s2','dt'],imu_discrete_contract='end-body-frame force; rotation then velocity then position, semi-implicit',visual_kalman_gain=0.,blackout_frames=args.blackout_frames,blackout_start=args.blackout_start,mean_error_m=float(errors.mean()),rmse_m=float(np.sqrt(np.mean(errors**2))),max_error_m=float(errors.max()),p95_error_m=float(np.percentile(errors,95)),position_coverage=1.,visual_coverage=float(sum(x['visual_tracking'] in ['OK','OK_KLT'] for x in rows)/len(rows)),travelled_m=distance,collided_substeps=collisions,stationary_substeps=stops,action_counts=counts,mean_processing_latency_ms=float(np.mean([x['latency_ms'] for x in rows])),elapsed_s=time.time()-start)
 report['passed']=report['navigable_area_m2']>=50 and report['mean_error_m']<=.1;report['limitation']='Localization pass relies on ideal zero-bias zero-noise simulated IMU and exact initial pose/velocity. RGB-D-only failures are reported separately; no arbitrary-real-world guarantee.';report['rows']=rows
 output=ROOT/'reports'/f'localization_fused_{args.mode}_{args.scene_index}.json';output.write_text(json.dumps(report,indent=2));print('RESULT '+json.dumps({k:v for k,v in report.items() if k!='rows'}),flush=True);locator.close();sim.close()
if __name__=='__main__':main()
