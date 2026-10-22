"""Convert the downloaded BSD PX4 Iris mesh to Habitat-compatible glTF."""
import hashlib
import json
from pathlib import Path
import numpy as np
import trimesh

root = Path(__file__).resolve().parents[1]
mesh = trimesh.load(str(root / 'assets/iris.stl'))
# Gazebo uses Z up. Habitat uses Y up, camera forward -Z.
transform = np.array([[0, -1, 0, 0], [0, 0, 1, 0], [-1, 0, 0, 0], [0, 0, 0, 1]])
mesh.apply_transform(transform)
mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, face_colors=[53, 111, 145, 255])
mesh.export(str(root / 'assets/iris.glb'))
record = {'name': 'PX4 Iris', 'source': 'https://github.com/PX4/PX4-SITL_gazebo-classic/tree/main/models/iris',
          'mesh_url': 'https://raw.githubusercontent.com/PX4/PX4-SITL_gazebo-classic/main/models/iris/meshes/iris.stl',
          'license': 'Upstream attribution retained; see PX4_SOURCE.txt', 'units': 'meters',
          'bounds': mesh.bounds.tolist(), 'sha256': hashlib.sha256((root / 'assets/iris.glb').read_bytes()).hexdigest()}
(root / 'assets/drone_manifest.json').write_text(json.dumps(record, indent=2))
print(json.dumps(record))
