#!/usr/bin/env bash
set -euo pipefail
cd /var/docker-apps/alphahunter
tar -xzf rollback/observation-20261015/source.tar.gz
python3 -c 'from pathlib import Path; Path("docker-compose.override.yml").write_text("services:\n  alphahunter:\n    image: alphahunter:rollback-observation-20261015\n    environment:\n      COLLECTOR_ENABLED: \"false\"\n")'
docker compose up -d --no-deps alphahunter
# Preserve production SQLite; the additional research table is backward compatible.
docker inspect --format 'image={{.Image}} health={{.State.Health.Status}}' alphahunter
