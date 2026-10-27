#!/bin/bash
# Step 1 of the H360 OBO demo: Keycloak realm, client, claim mappers and a test user.
#   realm   h360-obo
#   client  h360-mcp-client  (public, auth code + PKCE S256; direct grant ON for scripted testing only)
#   claims  aud += "h360-mcp", sf_username = the Salesforce Username the user maps to
#   tokens  access token 30 min
#   user    h360-demo-user   (random password -> ~/.config/keycloak-sandbox-clee/h360-demo-user, mode 600)
# Admin creds are read from ~/.config/keycloak-sandbox-clee/admin (line 1 user, line 2 password).
# Safe to re-run: existing objects return HTTP 409 and are left as they are.
set -euo pipefail
cd "$(dirname "$0")/.."
KCU=https://keycloak-1792182140.web.cloud-services.demos.mulesoft.com
SF_USERNAME=${SF_USERNAME:-h360-demo-user.ef2c09d411b9d5@example.com}
kc() { ./scripts/kc.sh "$@" | tail -1; }

echo "realm:";   kc POST /realms '{"realm":"h360-obo","enabled":true,"displayName":"H360 OBO demo","accessTokenLifespan":1800,"sslRequired":"external","registrationAllowed":false}'

echo "user profile (allow admin-set sf_username attribute):"
./scripts/kc.sh GET /realms/h360-obo/users/profile | sed '$d' \
  | python3 -c "import json,sys;d=json.load(sys.stdin);d['unmanagedAttributePolicy']='ADMIN_EDIT';print(json.dumps(d))" > /tmp/h360-up.json
kc PUT /realms/h360-obo/users/profile "$(cat /tmp/h360-up.json)"; rm -f /tmp/h360-up.json

echo "client:";  kc POST /realms/h360-obo/clients '{
 "clientId":"h360-mcp-client","name":"H360 MCP client (OBO demo)","enabled":true,"publicClient":true,
 "standardFlowEnabled":true,"directAccessGrantsEnabled":true,"serviceAccountsEnabled":false,
 "redirectUris":["http://localhost:*","http://127.0.0.1:*","https://h360-obo-demo-ky9yvn.aqopru.usa-e1.cloudhub.io/callback"],"webOrigins":["+"],
 "attributes":{"pkce.code.challenge.method":"S256",
   "post.logout.redirect.uris":"https://h360-obo-demo-ky9yvn.aqopru.usa-e1.cloudhub.io/##http://localhost:*##http://127.0.0.1:*"},
 "protocolMappers":[
  {"name":"aud-h360","protocol":"openid-connect","protocolMapper":"oidc-audience-mapper",
   "config":{"included.custom.audience":"h360-mcp","access.token.claim":"true","id.token.claim":"false"}},
  {"name":"sf_username","protocol":"openid-connect","protocolMapper":"oidc-usermodel-attribute-mapper",
   "config":{"user.attribute":"sf_username","claim.name":"sf_username","jsonType.label":"String",
             "access.token.claim":"true","id.token.claim":"true","userinfo.token.claim":"true"}}]}'

CRED=~/.config/keycloak-sandbox-clee/h360-demo-user
if [ ! -f "$CRED" ]; then
  umask 077
  printf '%s\n%s\n' h360-demo-user "$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')" > "$CRED"
fi
{ read -r U; read -r P; } < "$CRED"
echo "user:";    kc POST /realms/h360-obo/users "{\"username\":\"$U\",\"enabled\":true,\"emailVerified\":true,
 \"firstName\":\"H360\",\"lastName\":\"Demo User\",\"email\":\"h360-demo-user@example.invalid\",
 \"attributes\":{\"sf_username\":[\"$SF_USERNAME\"]},
 \"credentials\":[{\"type\":\"password\",\"value\":\"$P\",\"temporary\":false}]}"

echo "smoke test (token claims only, token itself not printed):"
curl -s "$KCU/realms/h360-obo/protocol/openid-connect/token" \
  --data-urlencode grant_type=password --data-urlencode client_id=h360-mcp-client \
  --data-urlencode "username=$U" --data-urlencode "password=$P" --data-urlencode scope=openid \
| python3 -c "
import json,sys,base64
d=json.load(sys.stdin)
if 'access_token' not in d: sys.exit(f'token request failed: {d}')
p=d['access_token'].split('.')[1]; p+='='*(-len(p)%4); c=json.loads(base64.urlsafe_b64decode(p))
print({k:c.get(k) for k in ['iss','aud','azp','sub','preferred_username','sf_username']}, 'expires_in', d['expires_in'])"
