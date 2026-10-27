#!/bin/bash
# kc.sh METHOD PATH [JSON]  -> Keycloak admin REST call (creds from ~/.config/keycloak-sandbox-clee/admin)
set -euo pipefail
KC=https://keycloak-1792182140.web.cloud-services.demos.mulesoft.com
{ read -r U; read -r P; } < "$HOME/.config/keycloak-sandbox-clee/admin"
TOK=$(curl -s -m 20 "$KC/realms/master/protocol/openid-connect/token" \
  --data-urlencode grant_type=password --data-urlencode client_id=admin-cli \
  --data-urlencode "username=$U" --data-urlencode "password=$P" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
curl -s -m 30 -X "$1" "$KC/admin$2" -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  ${3:+-d "$3"} -w '\nHTTP %{http_code}\n'
