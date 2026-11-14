#!/usr/bin/env bash
# Start the reverse-proxy compose example and check it serves (R-26, plan item E4).
# Uses only a pre-hashed key, exactly as the example ships. Needs docker compose v2, curl, python3.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
compose_file="$here/deploy/compose/docker-compose.yml"

key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
CEAP_API_KEY_HASHES="$(KEY="$key" python3 "$here/scripts/hash_api_key.py" --role auditor --id ci-smoke)"
export CEAP_API_KEY_HASHES

cleanup() {
  docker compose -f "$compose_file" logs --no-color ceap | tail -n 50 || true
  docker compose -f "$compose_file" down -v || true
}
trap cleanup EXIT

docker compose -f "$compose_file" up -d --build --wait --wait-timeout 180

# Caddy's certificate is self-signed ("tls internal"), hence -k
curl -fsSk --retry 10 --retry-delay 3 --retry-all-errors https://localhost/health
echo
status="$(curl -sk -o /dev/null -w '%{http_code}' https://localhost/tools)"
if [ "$status" != "401" ]; then
  echo "unauthenticated /tools returned $status, expected 401" >&2
  exit 1
fi
curl -fsSk -H "X-API-Key: $key" https://localhost/tools \
  | python3 -c 'import json,sys; t=json.load(sys.stdin); assert t, "no tools"; print(len(t), "tools")'
echo "compose smoke: OK"
