#!/usr/bin/env bash
# Run from any directory; all project paths resolve relative to this file.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
usage() {
    cat <<'HELP'
Usage: ./infrasage.sh [setup|start|investigate|status|help]
  setup        Install Python dependencies and build the Docker TCP probe image.
  start        Create/start the owned Voting App, verify a vote, then investigate.
  investigate  Run one investigation against the existing app, without restarting it.
  status       Show owned container states and Voting App URLs.
  help         Show this help.
Default: start. Requires Python 3.12+, Docker Engine and Docker Compose v2.
HELP
}
if (( $# > 1 )); then usage >&2; exit 2; fi
ACTION="${1:-start}"
case "$ACTION" in
    help|-h|--help) usage; exit 0 ;;
    setup|start|investigate|status) ;;
    *) usage >&2; exit 2 ;;
esac
command -v docker >/dev/null || { echo 'Install Docker Engine and Docker Compose v2 first.' >&2; exit 1; }
docker info >/dev/null || { echo 'Docker is unavailable. Start Docker and check your Docker permissions.' >&2; exit 1; }
if [[ "$ACTION" == setup ]]; then
    command -v python3 >/dev/null || { echo 'Install Python 3.12+ with venv support first.' >&2; exit 1; }
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "Python 3.12+ is required")'
    docker compose version >/dev/null
    if [[ ! -x .venv/bin/python ]]; then python3 -m venv .venv; fi
    .venv/bin/python -m pip install -r requirements.txt
    .venv/bin/python scripts/build_tcp_probe_image.py
    echo 'Setup complete. Next: ./infrasage.sh start'
    exit 0
fi
[[ -x .venv/bin/python ]] || { echo 'Run ./infrasage.sh setup first.' >&2; exit 1; }
.venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "Python 3.12+ is required; recreate .venv with a supported Python and rerun setup")'
.venv/bin/python -c 'import docker, yaml, requests' || { echo 'Run ./infrasage.sh setup to install dependencies.' >&2; exit 1; }
if [[ "$ACTION" == start ]]; then
    docker compose version >/dev/null
    if [[ -f lab/voting-app-manifest.json ]]; then
        .venv/bin/python scripts/setup_voting_app_lab.py --refresh
    else
        .venv/bin/python scripts/setup_voting_app_lab.py --create
    fi
fi
exec .venv/bin/python scripts/launch_voting_app.py "$ACTION"
