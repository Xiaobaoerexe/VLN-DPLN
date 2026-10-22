#!/usr/bin/env bash
# ONLINE build machine: packages the deployed platform, never changes old envs.
set -euo pipefail
SOURCE=${DRONENAV_SOURCE:-/home/abc/Documents/DroneNavTeaching}
OUT=${DRONENAV_BUNDLE:-/home/abc/Documents/DroneNavTeaching-offline-ubuntu24}
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ -f $HERE/build_payloads.sh ]] || HERE=$HERE/tools
MODE=${1:---all}
mkdir -p "$OUT"/{tools,payload,audit,validation}
if [[ $MODE = --all ]]; then
  bash "$HERE/build_payloads.sh"
  if ! docker image inspect dronenav/ubuntu-base:24.04.4 >/dev/null 2>&1; then
    mkdir -p "$OUT/preparation"
    curl -fLsS -o "$OUT/preparation/ubuntu-base-24.04.4-amd64.tar.gz" 'https://mirrors.tuna.tsinghua.edu.cn/ubuntu-cdimage/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.4-base-amd64.tar.gz'
    docker import "$OUT/preparation/ubuntu-base-24.04.4-amd64.tar.gz" dronenav/ubuntu-base:24.04.4
  fi
  bash "$HERE/prepare_debs.sh"
  python3 "$HERE/fetch_browser.py" "$OUT/payload/firefox-esr.tar.xz"
fi
if [[ $MODE = --all || $MODE = --refresh-app || $MODE = --stage-source ]]; then
  echo 'Creating application snapshot from the current finalized source.'
  git -C "$SOURCE" rev-parse HEAD > "$OUT/audit/source-git-commit.txt"
  git -C "$SOURCE" status --short > "$OUT/audit/source-git-status.txt"
  files=(README.md .gitignore .gitattributes app web navigation localization recognition scripts docs reports assets vendor packaging start.sh stop.sh supervisor.py)
  actual=();for f in "${files[@]}"; do [[ -e $SOURCE/$f ]] && actual+=("$f");done
  tar -C "$SOURCE" --exclude='.git' --exclude='*/__pycache__' --exclude='*.pyc' \
    --exclude='recognition/.venv' --exclude='vendor/InternNav/checkpoints' --exclude='vendor/InternNav/test_data' \
    --exclude='vendor/Pangolin-0.8/build' --exclude='vendor/ORB_SLAM3/build' \
    --exclude='localization/build/CMakeFiles' --exclude='localization/build/CMakeCache.txt' \
    --exclude='*.o' -cf - "${actual[@]}" | python3 "$HERE/teaching_docs.py" --filter-tar | pigz -1 -p 6 > "$OUT/payload/application.tar.gz.partial"
  mv "$OUT/payload/application.tar.gz.partial" "$OUT/payload/application.tar.gz"
fi
cp "$HERE/install.sh" "$OUT/install.sh"
cp "$HERE/build_offline.sh" "$OUT/build_offline.sh"
cp "$HERE/install_usage.txt" "$OUT/README.md"
cp "$HERE/install_usage.txt" "$OUT/tools/install_usage.txt"
for f in relocate.py configure_boot.py configure_service.py configure_browser.py verify_install.py verify_gpu_lifecycle.py collect_validation.py collect_fresh_install.py finalize_manifest.py teaching_docs.py; do cp "$HERE/$f" "$OUT/tools/$f";done
for f in build_payloads.sh prepare_debs.sh fetch_browser.py; do cp "$HERE/$f" "$OUT/tools/$f";done
chmod +x "$OUT/install.sh" "$OUT/build_offline.sh"
bash -n "$OUT/install.sh"
bash -n "$OUT/build_offline.sh"
[[ $MODE != --stage-source ]] || { echo 'Application staged; bundle not finalized yet.';exit 0; }
python3 "$HERE/finalize_manifest.py" "$OUT"
echo "Bundle ready: $OUT"
du -sh "$OUT"
