"""Render the actual downloaded Iris model in one OVON scene for visual QA."""
from pathlib import Path
import cv2
from app.simulator import DroneSimulator, EpisodeCatalog

root = Path(__file__).resolve().parents[1]
sim = DroneSimulator(EpisodeCatalog().choose(scene_index=1, episode_index=0))
try:
    rgb, depth, overview = sim.observe()
    cv2.imwrite(str(root / 'reports/drone_overview.jpg'), cv2.cvtColor(overview, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(root / 'reports/drone_front.jpg'), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    print(sim.metadata())
finally:
    sim.close()
