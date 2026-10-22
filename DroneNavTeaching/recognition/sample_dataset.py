"""Offline known-environment goal crop dataset; never used by runtime detector.

OVON goal annotations supply labels and target centres. No semantic geometry is
installed, so this explicitly measures image retrieval, not detection mAP.
"""
import argparse, collections, gzip, json, math, os, random, time
from pathlib import Path
import cv2
import habitat_sim
import numpy as np
import quaternion

ROOT = Path('/home/abc/Documents/DroneNavTeaching')
DATA = Path('/home/abc/Documents/VLN/ovon/data')

def simulator(scene):
    b = habitat_sim.SimulatorConfiguration()
    # OVON contains .basis.glb identifiers, but the supplied mesh is original .glb.
    b.scene_id = str(DATA / 'scene_datasets' / scene.replace('.basis.glb', '.glb'))
    b.enable_physics = False
    b.gpu_device_id = 0
    s = habitat_sim.CameraSensorSpec()
    s.uuid = 'rgb'; s.sensor_type = habitat_sim.SensorType.COLOR
    s.resolution = [480, 640]; s.position = [0., 1.4, 0.]; s.hfov = 90.
    a = habitat_sim.agent.AgentConfiguration(); a.sensor_specifications = [s]
    return habitat_sim.Simulator(habitat_sim.Configuration(b, [a]))

def pose(v):
    a = habitat_sim.AgentState(); a.position = np.array(v['position'])
    x,y,z,w = v['rotation']; a.rotation = np.quaternion(w,x,y,z)
    return a

def target_crop(rgb, viewpoint, goal):
    st = pose(viewpoint)
    v = quaternion.rotate_vectors(st.rotation.conjugate(), np.array(goal)-st.position-np.array([0,1.4,0]))
    if v[2] >= -.05: return None
    u = 320. + 320.*v[0]/-v[2]; w = 240.-320.*v[1]/-v[2]
    if u < 24 or u > 616 or w < 24 or w > 456: return None
    # Fixed 224 pixel window; target extent is not known without semantic mesh.
    half = 112
    x1=int(np.clip(u-half,0,640-224)); y1=int(np.clip(w-half,0,480-224))
    return rgb[y1:y1+224,x1:x1+224], [x1,y1,x1+224,y1+224], [float(u),float(w)]

def main():
    p=argparse.ArgumentParser();p.add_argument('--max-instances',type=int,default=4)
    p.add_argument('--append-missing',action='store_true');p.add_argument('--append-scenes',action='store_true');args=p.parse_args()
    out=ROOT/'assets/recognition/dataset'; out.mkdir(parents=True,exist_ok=True)
    previous=json.loads((out/'manifest.json').read_text()) if (args.append_missing or args.append_scenes) and (out/'manifest.json').exists() else {}
    existing={x['label'] for x in previous.get('records',[])} if args.append_missing else set()
    existing_scenes={x['scene'] for x in previous.get('records',[])}
    candidates=collections.defaultdict(list)
    files=sorted((DATA/'datasets/ovon/hm3d/val_unseen/content').glob('*.json.gz'))
    for f in files:
        if f.name.startswith('.'): continue
        d=json.load(gzip.open(f,'rt')); scene=d['episodes'][0]['scene_id']
        if args.append_scenes and scene in existing_scenes:continue
        for goals in d['goals_by_category'].values():
            for g in goals:
                if len(g.get('view_points',[]))>=2 and g['object_category'] not in existing:
                    candidates[g['object_category']].append((scene,g))
    rng=random.Random(20261020); selected=collections.defaultdict(list)
    for c,items in sorted(candidates.items()):
        rng.shuffle(items)
        if args.append_scenes:
            seen_scenes=set()
            for scene,g in items:
                if scene not in seen_scenes:selected[scene].append(g);seen_scenes.add(scene)
            continue
        # Prefer separate scenes before a second instance from the same scene.
        items.sort(key=lambda z:sum(1 for a in items if a[0]==z[0]))
        for scene,g in items[:args.max_instances]: selected[scene].append(g)
    records=previous.get('records',[]); rejected=previous.get('rejected',[]); begin=time.time()
    for n,(scene,goals) in enumerate(sorted(selected.items())):
        sim=simulator(scene)
        for g in goals:
            views=g['view_points']; chosen=[]
            # Inspect all annotated views, not just the first eight: some goals
            # are below the level camera's field of view at 1.4 m.
            for vp in views:
                v=vp['agent_state']; pos=np.array(v['position'])
                if any(np.linalg.norm(pos-np.array(x['position']))<.20 for x in chosen): continue
                if target_crop(np.zeros((480,640,3),np.uint8),v,g['position']) is None:continue
                chosen.append(v)
                if len(chosen)>=8:break
            # Additional horizontal views back from the annotated pose keep
            # low targets visible without lowering or pitching the camera.
            if len(chosen)<4:
                for vp in views[:12]:
                    v=vp['agent_state']; p0=np.array(v['position']); centre=np.array(g['position']); ray=p0-centre;ray[1]=0
                    if np.linalg.norm(ray)<.01:continue
                    ray/=np.linalg.norm(ray)
                    for back in [.5,1.,1.5,2.,3.]:
                        point=np.array(sim.pathfinder.snap_point(p0+ray*back))
                        if not np.all(np.isfinite(point)) or abs(point[1]-p0[1])>.15:continue
                        if any(np.linalg.norm(point-np.array(x['position']))<.20 for x in chosen):continue
                        dx,dz=centre[0]-point[0],centre[2]-point[2]
                        yaw=math.atan2(-dx,-dz);nv={'position':point.tolist(),'rotation':[0,math.sin(yaw/2),0,math.cos(yaw/2)]}
                        if target_crop(np.zeros((480,640,3),np.uint8),nv,g['position']) is None:continue
                        chosen.append(nv)
                        if len(chosen)>=8:break
                    if len(chosen)>=8:break
            instance=scene.split('/')[-2]+'_'+g['object_id']
            for k,v in enumerate(chosen):
                sim.get_agent(0).set_state(pose(v),reset_sensors=True)
                rgb=sim.get_sensor_observations()['rgb'][:,:,:3]
                crop=target_crop(rgb,v,g['position'])
                if crop is None: rejected.append({'instance':instance,'view':k,'reason':'target centre outside viewport'});continue
                patch,box,centre=crop
                split='gallery' if k%2==0 else 'test'
                name='%05d.jpg'%len(records); cv2.imwrite(str(out/name),cv2.cvtColor(patch,cv2.COLOR_RGB2BGR))
                records.append({'image':name,'split':split,'label':g['object_category'],'instance':instance,'scene':scene,
                                'position':v['position'],'rotation':v['rotation'],'bbox':box,'target_pixel':centre})
        sim.close(); print('scene',n+1,len(selected),'images',len(records),flush=True)
    manifest={'method':'OVON goal-centred 224x224 crops, camera 1.4m, HFOV90, 640x480',
              'split':'different positions, minimum 0.20m apart; alternating views, same known object instances allowed',
              'records':records,'rejected':rejected,'duration_s':time.time()-begin,'classes_in_tasks':49}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({'samples':len(records),'labels':len(set(x['label'] for x in records)),'counts':collections.Counter(x['split'] for x in records)},indent=2))

if __name__=='__main__':main()
