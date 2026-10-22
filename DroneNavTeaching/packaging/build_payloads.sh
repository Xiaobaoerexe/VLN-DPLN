#!/usr/bin/env bash
set -euo pipefail
SOURCE=${DRONENAV_SOURCE:-/home/abc/Documents/DroneNavTeaching}
OUT=${DRONENAV_BUNDLE:-/home/abc/Documents/DroneNavTeaching-offline-ubuntu24}
CONDA=${DRONENAV_CONDA:-/home/abc/anaconda3}
mkdir -p "$OUT"/{payload,audit,validation,debs/runtime,debs/driver,tools}
exec > >(tee -a "$OUT/audit/build-payloads.log") 2>&1
export PYTHONUNBUFFERED=1
pack_env() {
  local name=$1
  if [[ -f "$OUT/payload/env-$name.tar.gz" ]]; then echo "reuse env-$name"; return; fi
  echo "Packing environment $name $(date -Is)"
  "$CONDA/bin/conda-pack" -p "$CONDA/envs/$name" -o "$OUT/payload/env-$name.tar" --format tar \
    --ignore-editable-packages --ignore-missing-files \
    --exclude 'lib/python*/site-packages/__editable__*' \
    --exclude 'lib/python*/site-packages/easy-install.pth' \
    --exclude '*.egg-link' --exclude '*/__pycache__/*' --exclude '*.pyc' \
    --exclude '.condarc' --exclude '.netrc' --exclude 'etc/conda/env_vars.d/*'
  pigz -1 -p 6 "$OUT/payload/env-$name.tar"
}
for name in ovon internnav; do
  "$CONDA/envs/$name/bin/python" -m pip list --format=json > "$OUT/audit/pip-$name.json"
  "$CONDA/bin/conda" list -p "$CONDA/envs/$name" --json > "$OUT/audit/conda-$name.json"
done
pack_env ovon &
pid_ovon=$!
pack_env internnav &
pid_internnav=$!
if [[ ! -f "$OUT/payload/weights-dualvln.tar.gz" ]]; then
  echo "Packing ONLY the deployed 16.77GB DualVLN checkpoint"
  tar -C /home/abc/Documents/VLN/test/InternNav-main/checkpoints -cf - InternVLA-N1-DualVLN \
    | pigz -1 -p 6 > "$OUT/payload/weights-dualvln.tar.gz.partial"
  mv "$OUT/payload/weights-dualvln.tar.gz.partial" "$OUT/payload/weights-dualvln.tar.gz"
fi
if [[ ! -f "$OUT/payload/ovon-data.tar.gz" ]]; then
  echo "Packing all platform OVON data"
  tar --exclude='._*' -C /home/abc/Documents/VLN/ovon -cf - data | pigz -1 -p 4 > "$OUT/payload/ovon-data.tar.gz.partial"
  mv "$OUT/payload/ovon-data.tar.gz.partial" "$OUT/payload/ovon-data.tar.gz"
fi
if [[ ! -f "$OUT/payload/weights-depth-anything.tar.gz" ]]; then
  tar -C /home/abc/Documents/VLN/test/InternNav-main/checkpoints -czf "$OUT/payload/weights-depth-anything.tar.gz" depth_anything_v2_metric_hypersim_vits.pth
fi
if [[ ! -f "$OUT/payload/recognition-overlay.tar.gz" ]]; then
  tar -C "$SOURCE/recognition/.venv/lib/python3.10/site-packages" -czf "$OUT/payload/recognition-overlay.tar.gz" \
    ultralytics ultralytics-8.3.228.dist-info
fi
wait "$pid_ovon"
wait "$pid_internnav"
echo "Heavy payloads complete $(date -Is)"
du -sh "$OUT/payload"/*
