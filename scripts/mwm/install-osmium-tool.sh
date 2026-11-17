#!/usr/bin/env bash
# Needs root: install osmium-tool for vehicle_limits annotate (Geofabrik filter).
# pyosmium already works as a fallback; this is optional but faster for large PBFs.
# Run: sudo ./scripts/mwm/install-osmium-tool.sh
set -euo pipefail
apt-get update
apt-get install -y osmium-tool
osmium --version
