# H360 On-Behalf-Of (OBO): call Salesforce Headless 360 as the signed-in user

A working demo of **per-user access to the Salesforce Headless 360 (H360) MCP server**. A user
signs in with **Keycloak** or **Microsoft Entra ID**; **MuleSoft Omni Gateway** exchanges their
token for a Salesforce token **for the same person**; H360 then runs every call with that
user's Salesforce permissions. The app never holds a Salesforce credential.

![OBO sequence](docs/diagrams/h360-obo-sequence.png)

**Why:** agents usually call Salesforce as one shared integration user, so every action is
attributed to that account and every caller gets its (often admin) access. With OBO,
*Created By* shows the real person and Salesforce enforces that person's profile, permission
sets, field-level security and sharing on every call.

| | Keycloak | Entra ID |
|---|---|---|
| Demo app | https://h360-obo-demo-ky9yvn.aqopru.usa-e1.cloudhub.io | https://h360-obo-demo-entra-ky9yvn.aqopru.usa-e1.cloudhub.io |
| Gateway route | `/sfdc-headless-360-obo` | `/sfdc-headless-360-obo-entra` |
| User mapping | `sf_username` claim → User.Username | `oid` claim → User.FederationIdentifier |
| Diagram | [Keycloak](docs/diagrams/h360-obo-sequence.png) | [Entra](docs/diagrams/h360-obo-sequence-entra.png) |

Full walkthrough, every setting, the permissions model and troubleshooting:
**[docs/h360-obo-flow.md](docs/h360-obo-flow.md)**.

---

## Repository layout

| Path | What |
|---|---|
| [`mule-app/`](mule-app/) | Mule 4 demo app (CloudHub 2.0): login (OIDC auth code + PKCE), page, MCP calls to H360 through the gateway. One jar serves both IdPs via `idp.*` properties. |
| [`salesforce/classes/KeycloakOboTokenExchangeHandler.cls`](salesforce/classes/KeycloakOboTokenExchangeHandler.cls) | Apex token exchange handler: validates the IdP token (JWKS, `iss`, `aud`, `azp`, Entra `scp`) and picks the Salesforce user |
| [`scripts/setup-keycloak.sh`](scripts/setup-keycloak.sh) | creates the Keycloak realm `h360-obo`, client `h360-mcp-client`, mappers and demo user |
| [`scripts/kc.sh`](scripts/kc.sh) | Keycloak admin REST helper used by the setup script |
| [`scripts/test-obo.py`](scripts/test-obo.py) | end-to-end test: Keycloak login → token exchange → H360, directly or `--via-gateway=<URL>` |
| [`local-demo/app.py`](local-demo/app.py) | the first, Python-only version of the demo app (localhost:8790) |
| [`docs/`](docs/) | flow document and sequence diagrams (`.mmd` sources, `.png`, `.svg`) |

---

## Components and where they are configured

| Component | Where | Configured by |
|---|---|---|
| Keycloak realm `h360-obo` | `https://keycloak-1792182140.web.cloud-services.demos.mulesoft.com` | `scripts/setup-keycloak.sh` |
| Entra app **H360 OBO Demo** | tenant `e81fdb49-f453-4fb1-acec-589f688c68d6`, client `618c685b-60ab-4dd2-a825-147941207fde` | Entra admin center (manual) |
| Omni Gateway routes | `my-flex-gateway` (Sandbox), API instances `21251324` (Keycloak), `21257802` (Entra) | API Manager |
| Salesforce org | `trailsignup-ef2c09d411b9d5` | Setup + Tooling API |
| Demo apps | CloudHub 2.0 private space `clee-inc-ps`, Mule 4.12.5 Edge, Java 17 | `mule-app/pom.xml`, Runtime Manager properties |

---

## Setting it up from scratch

### 1. Identity provider

**Keycloak**
```bash
printf '%s\n%s\n' <admin-user> <admin-password> > ~/.config/keycloak-sandbox-clee/admin   # chmod 600
./scripts/setup-keycloak.sh          # realm, PKCE client, aud + sf_username mappers, demo user
```
Set each Keycloak user's `sf_username` attribute to their Salesforce username (admin-edit only).

**Entra ID** (admin center)
1. App registration, single tenant; **Web** redirect URI `https://<entra-app-host>/callback`
   (+ `http://localhost:8081/callback` for local runs).
2. *Expose an API*: Application ID URI `api://<client-id>`, scope `access_as_user`.
3. Manifest: `"requestedAccessTokenVersion": 2`.
4. *Certificates & secrets*: new client secret. Copy the **Value**, not the Secret ID.
5. Note each demo user's **Object ID** (`oid`).

### 2. Salesforce

1. **Apex class** `salesforce/classes/KeycloakOboTokenExchangeHandler.cls`: set the Keycloak
   issuer, Entra tenant and Entra app ID constants, then deploy (Tooling API or `sf project deploy`).
2. **Remote sites** for the JWKS callouts: the Keycloak host and `https://login.microsoftonline.com`.
3. **Setup → Token Exchange Handlers**: register the class, token type *access token*, enable it
   on the External Client App as its default handler.
4. **External Client App** (here *Gemini MCP POC*): enable **Token Exchange Flow**; scope `mcp_api`;
   Permitted users = **Admin approved users are pre-authorized**.
5. **Permission set** (here *H360 MCP App Access*) with access to that app; assign it to every
   user who should be able to sign in, and to the app's client-credentials run-as user if that
   flow is used too. Add the permission set in the app's **Policies** page.
6. **Users**: Keycloak users map by **Username**; Entra users map by **Federation ID = Entra `oid`**.

### 3. Omni Gateway (one API instance per IdP, upstream `https://api.salesforce.com/platform/mcp/v1/platform/headless-360`)

| Policy | Settings |
|---|---|
| **JWT Validation** (inbound) | JWKS = the IdP's keys URL; validate `aud` (Keycloak `h360-mcp`, Entra = client ID); `exp` mandatory; **client ID validation off** |
| **OAuth 2.0 OBO Credential Injection** (outbound, on the upstream) | flow token exchange; token endpoint `https://<my-domain>.my.salesforce.com/services/oauth2/token`; client ID/secret of the External Client App; subject token type access token; target type audience = the My Domain URL |

Keep a separate, policy-free route if other tools send a Salesforce token directly.

### 4. Demo app

```bash
cd mule-app
JAVA_HOME=/Library/Java/JavaVirtualMachines/zulu-17.jdk/Contents/Home mvn -B clean package -DskipTests
```
Deploy the jar to CloudHub 2.0 (1 replica, 0.1 vCore, public URL, Object Store v2 off).
`config.yaml` defaults to Keycloak. For Entra, set these Runtime Manager properties:

| Property | Value |
|---|---|
| `idp.label` / `idp.detail` | `Microsoft Entra ID` / `Entra tenant <name>` |
| `idp.authorizeUrl`, `idp.tokenUrl`, `idp.logoutUrl` | `https://login.microsoftonline.com/<tenant>/oauth2/v2.0/{authorize,token,logout}` |
| `idp.clientId` | the Entra client ID |
| `idp.scope` | `openid profile api://<client-id>/access_as_user` |
| `idp.postLogoutPath` | `/callback` |
| `h360.gatewayPath` | `/sfdc-headless-360-obo-entra` |
| `idp.clientSecret` | **Secured** property: the Entra secret value |

Add `https://<app-host>/callback` to the IdP's allowed redirect URIs (Keycloak: also the
post-logout URI `https://<app-host>/`).

---

## Running the demo

1. Open the app, **Log in with Keycloak / Microsoft Entra ID** as the demo user.
2. **Top 3 opportunities**: the page says which Salesforce user ran the query.
3. **Try edit** a deal owned by someone else → ✖ *Insufficient Privileges*; your own → ✔ allowed.
4. **Create opportunity** → *Created By* / *Owner* = the signed-in user.
5. Log out, log in as the admin user, repeat: the same calls are now allowed.

Scripted check (Keycloak, direct grant):
```bash
python3 scripts/test-obo.py                                   # exchange done by the script
python3 scripts/test-obo.py --via-gateway=https://my-flex-gateway-ky9yvn.aqopru.usa-e1.cloudhub.io/sfdc-headless-360-obo/
```

---

## Credentials

**Nothing secret is in this repository.** Scripts read credentials from owner-only files;
deployed apps use Runtime Manager **secured** properties; the gateway stores the OBO client
secret as a sensitive policy field.

| Credential | Local file (mode 600) | Used by |
|---|---|---|
| Keycloak admin | `~/.config/keycloak-sandbox-clee/admin` | `scripts/kc.sh` |
| Keycloak demo users | `~/.config/keycloak-sandbox-clee/{h360-demo-user,chiew-lee}` | `scripts/test-obo.py`, manual login |
| Salesforce External Client App | `~/.config/sprawl-scanner/salesforce_connected_app` | `scripts/test-obo.py`, gateway OBO policy |
| Entra client secret | `~/.config/entra-h360-obo/client_secret` | `idp.clientSecret` on the Entra deployment |

A [gitleaks](https://github.com/gitleaks/gitleaks) pre-commit hook blocks commits containing
secrets; CI runs the same scan. Enable once per clone:
```bash
brew install gitleaks
git config core.hooksPath .githooks
```

---

## Before using this beyond a demo

- Rotate the secrets that were shared during the build (Keycloak admin, Entra client secret,
  External Client App secret, Anypoint Connected App secret).
- Turn off **direct grant** on the Keycloak client (switch `test-obo.py` to a browser login).
- Use a **dedicated External Client App** for OBO with only token exchange enabled.
- Map Keycloak users on a stable identifier (e.g. `sub` → Federation ID) instead of `sf_username`;
  refuse privileged users in the handler.
