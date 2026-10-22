#!/usr/bin/env bash
# Completely offline installer. No curl, wget, pip install, conda install or git.
set -euo pipefail
BUNDLE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
INSTALL_USER=${SUDO_USER:-$(id -un)}
INSTALL_HOME=$(getent passwd "$INSTALL_USER" | cut -d: -f6)
PREFIX=${DRONENAV_PREFIX:-"$INSTALL_HOME/Documents/DroneNavTeaching"}
NO_START=0; TEST_NO_GPU=0; RESUME=0; REBOOT=0
while (($#)); do
  case "$1" in
    --prefix) PREFIX=$2; shift 2;;
    --user) INSTALL_USER=$2; shift 2;;
    --no-start) NO_START=1;shift;;
    --test-no-gpu) TEST_NO_GPU=1;NO_START=1;shift;;
    --resume) RESUME=1;shift;;
    --reboot) REBOOT=1;shift;;
    *) echo "Unknown argument: $1" >&2;exit 2;;
  esac
done
[[ $PREFIX = /* && $PREFIX != / && $PREFIX != /home && $PREFIX != /usr && $PREFIX != *$'\n'* ]] || { echo 'Invalid installation prefix'; exit 2; }
if ((EUID!=0)); then
  exec sudo -- /bin/bash "$BUNDLE/install.sh" --prefix "$PREFIX" --user "$INSTALL_USER" \
    $([[ $NO_START = 1 ]] && echo --no-start) $([[ $TEST_NO_GPU = 1 ]] && echo --test-no-gpu) \
    $([[ $RESUME = 1 ]] && echo --resume) $([[ $REBOOT = 1 ]] && echo --reboot)
fi
source /etc/os-release
[[ ${ID:-} = ubuntu && ${VERSION_ID:-} = 24.04 && $(uname -m) = x86_64 ]] || { echo 'Requires Ubuntu 24.04 x86_64';exit 2; }
# A network namespace physically prevents installation-time Internet access.
# A validation container already has --network none and may disallow unshare.
if [[ ${DRONENAV_OFFLINE_NAMESPACE:-0} != 1 ]]; then
  exec unshare --net -- env DRONENAV_OFFLINE_NAMESPACE=1 /bin/bash "$BUNDLE/install.sh" \
    --prefix "$PREFIX" --user "$INSTALL_USER" \
    $([[ $NO_START = 1 ]] && echo --no-start) $([[ $TEST_NO_GPU = 1 ]] && echo --test-no-gpu) \
    $([[ $RESUME = 1 ]] && echo --resume) $([[ $REBOOT = 1 ]] && echo --reboot)
fi
if [[ -d $PREFIX && -n $(find "$PREFIX" -mindepth 1 -maxdepth 1 -print -quit) && ! -f $PREFIX/.dronenav-offline-install ]]; then
  echo "Refusing to overwrite an unmanaged directory: $PREFIX" >&2;exit 2
fi
mkdir -p "$PREFIX" "$PREFIX/logs" "$PREFIX/state"
touch "$PREFIX/.dronenav-offline-install"
exec 9>"$PREFIX/state/install.lock"
flock -n 9 || { echo 'Another installation is already running for this prefix';exit 2; }
exec > >(tee -a "$PREFIX/logs/offline-install.log") 2>&1
echo "Offline installation: $PREFIX, user=$INSTALL_USER, $(date -Is)"
cd "$BUNDLE"
if ((RESUME)) && [[ -f $PREFIX/state/payloads.done && -f $PREFIX/state/driver-debs.done && -f $BUNDLE/RESUME_SHA256SUMS ]]; then
  sha256sum -c RESUME_SHA256SUMS > "$PREFIX/logs/resume-integrity.log" || { echo 'Persisted installer checksum mismatch';exit 3; }
else
  sha256sum -c SHA256SUMS > "$PREFIX/logs/integrity.log" || { echo 'Bundle checksum mismatch';exit 3; }
fi
available=$(df --output=avail -B1 "$PREFIX" | tail -1)
[[ $available -ge 55000000000 || -f $PREFIX/state/payloads.done ]] || { echo 'Need at least 55GB free space at destination (in addition to bundle).';exit 3; }
export DEBIAN_FRONTEND=noninteractive
mkdir -p "$PREFIX/state/empty-sources.d"
touch "$PREFIX/state/empty-sources.list"
APT=(apt-get -y --no-download --no-install-recommends -o "Dir::Etc::sourcelist=$PREFIX/state/empty-sources.list" -o "Dir::Etc::sourceparts=$PREFIX/state/empty-sources.d" -o Acquire::Retries=0)
install_debs() {
  local group=$1 cache="$PREFIX/state/apt-cache/$1"
  mkdir -p "$cache/partial"
  for deb in "$BUNDLE/debs/$group/"*.deb; do
    [[ -f $cache/$(basename "$deb") ]] || cp -a --reflink=auto "$deb" "$cache/"
  done
  "${APT[@]}" -o "Dir::Cache::archives=$cache/" install "$BUNDLE/debs/$group/"*.deb
}
if [[ ! -f $PREFIX/state/runtime-debs.done ]]; then
  echo 'Installing bundled Ubuntu runtime packages; all network sources disabled.'
  install_debs runtime
  touch "$PREFIX/state/runtime-debs.done"
fi
if [[ ! -f $PREFIX/state/payloads.done ]]; then
  echo 'Extracting all source, datasets, pretrained weights and Python environments.'
  tar -xzf "$BUNDLE/payload/application.tar.gz" -C "$PREFIX"
  mkdir -p "$PREFIX/runtime/ovon" "$PREFIX/runtime/internnav" "$PREFIX/vendor/InternNav/checkpoints" "$PREFIX/browser"
  tar -xzf "$BUNDLE/payload/env-ovon.tar.gz" -C "$PREFIX/runtime/ovon"
  tar -xzf "$BUNDLE/payload/env-internnav.tar.gz" -C "$PREFIX/runtime/internnav"
  tar -xzf "$BUNDLE/payload/recognition-overlay.tar.gz" -C "$PREFIX/runtime/internnav/lib/python3.10/site-packages"
  tar -xzf "$BUNDLE/payload/ovon-data.tar.gz" -C "$PREFIX"
  tar -xzf "$BUNDLE/payload/weights-dualvln.tar.gz" -C "$PREFIX/vendor/InternNav/checkpoints"
  tar -xJf "$BUNDLE/payload/firefox-esr.tar.xz" -C "$PREFIX/browser"
  "$PREFIX/runtime/ovon/bin/python" "$PREFIX/runtime/ovon/bin/conda-unpack"
  "$PREFIX/runtime/internnav/bin/python" "$PREFIX/runtime/internnav/bin/conda-unpack"
  python3 "$BUNDLE/tools/relocate.py" "$PREFIX"
  touch "$PREFIX/state/payloads.done"
fi
if [[ ! -f $PREFIX/vendor/InternNav/checkpoints/depth_anything_v2_metric_hypersim_vits.pth ]]; then
  tar -xzf "$BUNDLE/payload/weights-depth-anything.tar.gz" -C "$PREFIX/vendor/InternNav/checkpoints"
fi
python3 "$BUNDLE/tools/teaching_docs.py" "$PREFIX"
export DRONENAV_ROOT=$PREFIX DRONENAV_DATA_ROOT=$PREFIX/data
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
export YOLO_OFFLINE=true YOLO_AUTOINSTALL=false WANDB_MODE=disabled
export PYTHONPATH=$PREFIX PYTHONNOUSERSITE=1
if ! nvidia-smi >/dev/null 2>&1 && [[ ! -f $PREFIX/state/driver-debs.done ]]; then
  echo 'Installing bundled NVIDIA userspace, signed kernel, firmware and precompiled modules.'
  install_debs driver
  touch "$PREFIX/state/driver-debs.done"
fi
echo 'Checking all offline imports, assets, local tokenizer, retrieval and native SLAM linkage.'
python3 "$BUNDLE/tools/verify_install.py" "$PREFIX" --cpu > "$PREFIX/logs/offline-cpu-verification.json"
if ((TEST_NO_GPU)); then
  echo 'CPU/container validation complete. GPU/driver/kernel reboot NOT validated in this mode.'
  exit 0
fi
if ! nvidia-smi >/dev/null 2>&1; then
  echo 'No working NVIDIA driver. Installing bundled signed Ubuntu HWE kernel and NVIDIA driver packages.'
  modprobe nvidia 2>/dev/null || true
  if ! nvidia-smi >/dev/null 2>&1; then
    if ((RESUME)); then echo 'NVIDIA is still unavailable after reboot; inspect kernel/Secure Boot/hardware and offline-install.log.';exit 5;fi
    python3 "$BUNDLE/tools/configure_boot.py" "$BUNDLE" "$PREFIX" "$INSTALL_USER" "$NO_START"
    echo 'All software has been installed offline. A reboot is required to activate the bundled kernel/driver.'
    echo 'A verified persistent copy of the installer will resume automatically on boot; the USB bundle is no longer needed.'
    if ((REBOOT)); then systemctl reboot; fi
    exit 0
  fi
fi
python3 "$BUNDLE/tools/verify_install.py" "$PREFIX" --gpu > "$PREFIX/logs/offline-gpu-verification.json"
uid=$(id -u "$INSTALL_USER");group=$(id -gn "$INSTALL_USER")
chown -R "$INSTALL_USER:$group" "$PREFIX"
if [[ -d /run/systemd/system ]]; then
  python3 "$BUNDLE/tools/configure_service.py" "$PREFIX" "$INSTALL_USER"
  runuser -u "$INSTALL_USER" -- "$PREFIX/runtime/ovon/bin/python" "$BUNDLE/tools/configure_browser.py" "$PREFIX" "$NO_START"
  systemctl daemon-reload
  systemctl disable dronenav-install-resume.service >/dev/null 2>&1 || true
  systemctl disable dronenav-teaching.service >/dev/null 2>&1 || true
  if ((!NO_START)); then
    systemctl start dronenav-teaching.service
    echo 'Platform service started. Allow up to 3 minutes for all local models to become ready.'
  fi
elif ((!NO_START)); then
  echo 'A running systemd Desktop installation is required for automatic service startup.';exit 4
fi
touch "$PREFIX/state/install.complete"
echo "Installation complete: $PREFIX"
echo 'Local interface: http://127.0.0.1:5800/'
