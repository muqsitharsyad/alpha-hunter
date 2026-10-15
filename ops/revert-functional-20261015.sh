#!/usr/bin/env bash
set -euo pipefail
cd /var/docker-apps/alphahunter
# Existing image, never rebuild potentially changed source during rollback.
python3 -c 'from pathlib import Path; Path("docker-compose.override.yml").write_text("services:\n  alphahunter:\n    image: alphahunter:rollback-functional-20261015\n")'
cp rollback/functional-safety-20261015/backend/app/*.py backend/app/
docker compose up -d --no-deps alphahunter
# Existing paper database stays in place; do not overwrite operational data.
docker inspect --format 'image={{.Image}} health={{.State.Health.Status}}' alphahunter
