"""Record exact reused checkpoint files and optionally compare public LFS hashes."""
import hashlib
import json
from pathlib import Path
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "vendor/InternNav/checkpoints/InternVLA-N1-DualVLN"


def main():
    index = json.loads((MODEL / "model.safetensors.index.json").read_text())
    result = {"source": str(MODEL.resolve()), "public_model": "InternRobotics/InternVLA-N1-DualVLN",
              "index_metadata": index["metadata"], "parameter_entries": len(index["weight_map"]), "files": []}
    remote_hashes = {}
    try:
        url = "https://hf-mirror.com/api/models/InternRobotics/InternVLA-N1-DualVLN?blobs=true"
        response = requests.get(url, timeout=12)
        response.raise_for_status()
        public = response.json()
        result["public_revision"] = public.get("sha")
        for sibling in public.get("siblings", []):
            if "lfs" in sibling:
                remote_hashes[sibling["rfilename"]] = sibling["lfs"].get("sha256")
    except Exception as exc:
        result["public_hash_lookup_error"] = str(exc)
    for name in sorted(set(index["weight_map"].values())):
        path = MODEL / name
        begin = time.perf_counter()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
        row = {"name": name, "size": path.stat().st_size, "sha256": digest.hexdigest(),
               "hash_elapsed_s": time.perf_counter()-begin}
        expected = remote_hashes.get(name)
        if expected:
            row.update(public_lfs_sha256=expected, matches_public_lfs_sha256=expected == row["sha256"])
        result["files"].append(row)
        print(json.dumps(row), flush=True)
    result["all_shards_exist"] = all(x["size"] > 1024 for x in result["files"])
    result["total_checkpoint_bytes"] = sum(x["size"] for x in result["files"])
    (ROOT / "docs/model_weight_manifest.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
