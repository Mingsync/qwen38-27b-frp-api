#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
umask 077
if [[ ! -e .env ]]; then cp .env.example .env; fi
mkdir -p secrets
chmod 700 secrets
if [[ ! -e secrets/api_key ]]; then
  python3 - <<'PY'
import secrets
from pathlib import Path
Path('secrets/api_key').write_text('sk-local-' + secrets.token_urlsafe(36) + '\n')
PY
fi
chmod 600 .env secrets/api_key
printf '%s\n' 'Configuration initialized. Edit .env and (optionally) secrets/api_key. Existing values were preserved.'
