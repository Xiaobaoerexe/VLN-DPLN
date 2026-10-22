from pathlib import Path
import subprocess
root=Path('/home/abc/Documents/DroneNavTeaching')
orb=root/'vendor/ORB_SLAM3'
p=orb/'CMakeLists.txt';text=p.read_text();text=text.split('# Build examples')[0];text=text.replace('-std=c++11','-std=c++14');p.write_text(text)
p=orb/'include/System.h';text=p.read_text();needle='    int GetTrackingState();';
if 'GetTeachingAtlas' not in text:text=text.replace(needle,needle+'\n    Atlas* GetTeachingAtlas() { return mpAtlas; } // read-only teaching IPC adapter')
p.write_text(text)
commands=[['cmake','-S',str(orb/'Thirdparty/DBoW2'),'-B',str(orb/'Thirdparty/DBoW2/build'),'-DCMAKE_BUILD_TYPE=Release'],['cmake','--build',str(orb/'Thirdparty/DBoW2/build'),'-j8'],['cmake','-S',str(orb),'-B',str(orb/'build'),'-DCMAKE_BUILD_TYPE=Release','-DPangolin_DIR='+str(root/'vendor/pangolin-install/lib/cmake/Pangolin')],['cmake','--build',str(orb/'build'),'-j8'],['cmake','-S',str(root/'localization'),'-B',str(root/'localization/build'),'-DCMAKE_BUILD_TYPE=Release','-DPangolin_DIR='+str(root/'vendor/pangolin-install/lib/cmake/Pangolin')],['cmake','--build',str(root/'localization/build'),'-j4']]
subprocess.run(['tar','-xf',str(orb/'Vocabulary/ORBvoc.txt.tar.gz'),'-C',str(orb/'Vocabulary')],check=True)
for cmd in commands:
 print('BUILD',cmd,flush=True);subprocess.run(cmd,check=True)
