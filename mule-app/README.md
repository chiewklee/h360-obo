# H360 On-Behalf-Of demo (Mule app)

A small Mule 4 web app that shows an AI-style client calling the **Salesforce Headless 360
(H360) MCP server as the signed-in person**, not as a shared integration user.

```
Browser ── Log in with Keycloak (OIDC auth code + PKCE)
   │
Mule app (this) ── MCP JSON-RPC over HTTP, Authorization: Bearer <user's Keycloak token>
   │
Omni Gateway  /sfdc-headless-360-obo
   ├ inbound   JWT Validation          Keycloak JWKS, aud h360-mcp, exp
   └ outbound  OAuth 2.0 OBO Credential Injection
               RFC 8693 token exchange at Salesforce -> token for the same user
   │
Salesforce H360 MCP ── runs every call as that user (their profile, sharing, layouts)
```

The app holds **no Salesforce credentials and no secrets**: the Keycloak client is public
(PKCE) and the gateway does the token exchange.

## What the page does

| Action | H360 tool | What it shows |
|---|---|---|
| Log in with Keycloak | – | real Keycloak login page, realm `h360-obo` |
| Top 3 opportunities | `dispatch_readonly` (SOQL) + `/chatter/users/me` | "Salesforce ran this query as *&lt;user&gt;*" |
| Try edit (each row) | `dispatch` PATCH, same Amount written back | own deal ✔ allowed, others' deals ✖ *Insufficient Privileges* |
| Create opportunity | `dispatch` POST | Created By / Owner = the signed-in user |
| Delete (own rows) | `dispatch` DELETE | reset between demo runs |

## Layout

| File | Purpose |
|---|---|
| `src/main/mule/h360-obo-demo.xml` | flows: page, `/login`, `/callback`, `/logout`, `/api/*`; sub-flows for the MCP handshake and `tools/call` |
| `src/main/mule/global.xml` | HTTP listener, Keycloak + gateway request configs, in-memory session Object Store |
| `src/main/resources/config.yaml` | Keycloak host/realm/client, gateway host/path, demo Account Id |
| `src/main/resources/dw/sf.dwl` | extracts Salesforce's error message from an H360 tool result |
| `src/main/resources/web/index.html` | the page (Salesforce palette); `{{WHO}}` / `{{HIDDEN}}` filled in by the `page` flow |

Sessions: an HttpOnly `sid` cookie; tokens stay in a non-persistent Object Store (one replica).
The redirect URI is built from the request (`X-Forwarded-Proto` + `Host`), so the same jar works
locally (`http://localhost:8081`) and on CloudHub.

## Build and deploy

```bash
JAVA_HOME=/Library/Java/JavaVirtualMachines/zulu-17.jdk/Contents/Home mvn -B clean package -DskipTests
```

CloudHub 2.0: Mule **4.12.5 Edge**, Java 17, private space `clee-inc-ps` (Sandbox), 1 replica,
0.1 vCore, public URL, Object Store v2 off. Deployed by publishing the jar to Exchange
(`h360-obo-demo`, type app) and creating the deployment through the AMC API; the
`<cloudhub2Deployment>` block in `pom.xml` has the same settings for `mvn deploy -DmuleDeploy`.

Running elsewhere needs the app's `https://<host>/callback` added to the Keycloak client's
*Valid redirect URIs* (post-logout redirect is `+`, i.e. the same list).

## Prerequisites (set up separately)

Full flow, configuration of every component and troubleshooting:
[`../docs/h360-obo-flow.md`](../docs/h360-obo-flow.md).

See the repo root (`../scripts`, `../salesforce`) for the Keycloak realm/client/user scripts, the Apex
token exchange handler (`KeycloakOboTokenExchangeHandler`) and the end-to-end test
(`scripts/test-obo.py`). Salesforce side: External Client App with Token Exchange enabled,
handler registered, permitted users = admin pre-approved via the `H360 MCP App Access`
permission set.

## Gotchas hit while building

- H360 `dispatch` enforces page-layout rules: Opportunity create needs Account, Currency and
  Forecast category, and a field shown read-only anywhere on the layout (even a mobile card) is
  refused.
- `flow-ref` with `target=` only returns the sub-flow's payload; variables set inside it are not
  visible to the caller.
- DataWeave infix calls bind loosely: write `($ startsWith 'x') and (...)`, `(n mod 4) == 0`.
- DataWeave can't write `text/html`; write `text/plain` and set the mime type on the payload.
