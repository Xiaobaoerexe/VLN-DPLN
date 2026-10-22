#!/usr/bin/env bash
set -euo pipefail
DRONENAV_ROOT="${DRONENAV_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)}"
export DRONENAV_ROOT
exec "${DRONENAV_LAUNCHER_PYTHON:-python3}" "$DRONENAV_ROOT/supervisor.py" --open-browser "$@"
