#!/usr/bin/env bash
# ONLINE PREPARATION ONLY. The final install.sh never calls this file.
set -euo pipefail
OUT=${DRONENAV_BUNDLE:-/home/abc/Documents/DroneNavTeaching-offline-ubuntu24}
IMAGE=dronenav/ubuntu-base:24.04.4
mkdir -p "$OUT"/{debs/runtime,debs/driver,audit}
docker run --rm --name dronenav-deb-preparation -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -v "$OUT:/bundle" "$IMAGE" bash -euxo pipefail -c '
  rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*
  printf "%s\n" "deb http://mirrors.tuna.tsinghua.edu.cn/ubuntu noble main restricted universe multiverse" \
    "deb http://mirrors.tuna.tsinghua.edu.cn/ubuntu noble-updates main restricted universe multiverse" \
    "deb http://mirrors.tuna.tsinghua.edu.cn/ubuntu noble-security main restricted universe multiverse" >/etc/apt/sources.list
  apt-get update
  runtime=(python3 ca-certificates gcc g++ libc6-dev libopencv-core406t64 libopencv-highgui406t64 \
    libopencv-calib3d406t64 libopencv-features2d406t64 libopencv-imgproc406t64 libopencv-imgcodecs406t64 \
    libboost-serialization1.83.0 libglew2.2 libgl1 libopengl0 libegl1 libgl1-mesa-dri \
    libxkbcommon0 libxkbcommon-x11-0 libgtk-3-0t64 libasound2t64 libdbus-glib-1-2 libnss3 \
    libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libxtst6 libpci3 \
    pciutils xdg-utils dbus-x11 xz-utils binutils file procps libeigen3-dev patchelf)
  driver=(nvidia-driver-595-open linux-image-generic-hwe-24.04 \
    linux-modules-nvidia-595-open-generic-hwe-24.04 linux-headers-generic-hwe-24.04 nvidia-dkms-595-open-)
  mkdir -p /bundle/debs/runtime/partial /bundle/debs/driver/partial
  printf "%s\n" "${runtime[@]}" > /bundle/audit/runtime-apt-targets.txt
  printf "%s\n" "${driver[@]}" > /bundle/audit/driver-apt-targets.txt
  apt-get -y --download-only --no-install-recommends -o Dir::Cache::archives=/bundle/debs/runtime/ install "${runtime[@]}"
  apt-get -y --download-only --no-install-recommends -o Dir::Cache::archives=/bundle/debs/driver/ install "${driver[@]}"
  for group in runtime driver; do
    find /bundle/debs/$group -name "*.deb" -print0 | sort -z | xargs -0 dpkg-deb -W > /bundle/audit/debs-$group.tsv
  done
  cat /etc/os-release > /bundle/audit/clean-base-os-release.txt
  apt-cache policy nvidia-driver-590-open linux-image-generic-hwe-24.04 \
    linux-modules-nvidia-590-open-generic-hwe-24.04 > /bundle/audit/driver-apt-policy.txt
  find /bundle/debs -type f -name lock -delete
  chmod -R a+rX /bundle/debs
  chown -R "$HOST_UID:$HOST_GID" /bundle/debs /bundle/audit
'
echo "Offline Debian closure prepared"
du -sh "$OUT/debs"/*
