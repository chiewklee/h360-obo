#!/usr/bin/env python3
"""End-to-end OBO test: Keycloak user token -> Salesforce token exchange -> H360 MCP as that user.

  1. Log in to Keycloak (realm h360-obo) as h360-demo-user (direct grant, test only).
  2. Exchange the Keycloak access token at Salesforce /services/oauth2/token (RFC 8693);
     the KeycloakOboTokenExchangeHandler Apex class validates it and picks the user.
  3. Show who Salesforce thinks we are (userinfo).
  4. Call H360 over MCP with the exchanged token: read the top 3 opportunities, then
     try a no-op edit (same Amount) of the Q4 Demo Deal, which the demo user does not own.
     With Opportunity sharing at Public Read Only, the read works and the edit is refused.

Credentials (never printed):
  ~/.config/keycloak-sandbox-clee/h360-demo-user    line 1 username, line 2 password
  ~/.config/sprawl-scanner/salesforce_connected_app  line 1 client id, line 2 client secret
Usage: python3 scripts/test-obo.py [--via-gateway[=<gateway MCP URL>]]
  --via-gateway sends the Keycloak token to the Omni gateway (JWT Validation + OBO policies)
  and skips steps 2-3; the gateway does the exchange.
Standard library only.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

KC_TOKEN = ("https://keycloak-1792182140.web.cloud-services.demos.mulesoft.com"
            "/realms/h360-obo/protocol/openid-connect/token")
SF = "https://trailsignup-ef2c09d411b9d5.my.salesforce.com"
H360_DIRECT = "https://api.salesforce.com/platform/mcp/v1/platform/headless-360"
H360_GATEWAY = "https://my-flex-gateway-ky9yvn.aqopru.usa-e1.cloudhub.io/sfdc-headless-360/"
Q4_DEMO_DEAL = "006gL00000WhibFQAR"


def lines(path):
    with open(os.path.expanduser(path)) as f:
        return [l.strip() for l in f.read().splitlines()[:2]]


def http(url, data=None, headers=None, method=None):
    body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def claims(jwt):
    p = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def step(title):
    print(f"\n== {title}")


def mcp_result(text):
    """Streamable HTTP may answer as JSON or as SSE; return the JSON-RPC message."""
    if text.lstrip().startswith("{"):
        return json.loads(text)
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise ValueError(f"unexpected MCP response: {text[:200]}")


class Mcp:
    def __init__(self, url, token):
        self.url, self.token, self.session, self.n = url, token, None, 0

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self.n += 1
            msg["id"] = self.n
        h = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self.session:
            h["mcp-session-id"] = self.session
        status, headers, text = http(self.url, json.dumps(msg).encode(), h, "POST")
        self.session = self.session or {k.lower(): v for k, v in headers.items()}.get("mcp-session-id")
        if notify:
            return None
        if status >= 400:
            sys.exit(f"MCP {method} -> HTTP {status}: {text[:300]}")
        return mcp_result(text)

    def tool(self, name, args):
        res = self.send("tools/call", {"name": name, "arguments": args})["result"]
        return json.loads(res["content"][0]["text"]) if res.get("content") else res


def main():
    gw = next((a for a in sys.argv[1:] if a.startswith("--via-gateway")), None)
    via_gateway = gw is not None
    gateway_url = gw.split("=", 1)[1] if gw and "=" in gw else H360_GATEWAY

    step("1. Keycloak login (h360-demo-user)")
    user, pw = lines("~/.config/keycloak-sandbox-clee/h360-demo-user")
    status, _, text = http(KC_TOKEN, {"grant_type": "password", "client_id": "h360-mcp-client",
                                      "username": user, "password": pw, "scope": "openid"})
    if status != 200:
        sys.exit(f"Keycloak login failed: HTTP {status} {text[:300]}")
    kc_token = json.loads(text)["access_token"]
    c = claims(kc_token)
    print({k: c.get(k) for k in ("iss", "aud", "azp", "preferred_username", "sf_username")})

    if via_gateway:
        # The gateway validates the Keycloak token and does the exchange (OBO policy).
        step(f"2-3. skipped: Omni gateway exchanges the token ({gateway_url})")
        run_h360(gateway_url, kc_token, "Omni gateway, Keycloak token")
        return

    step("2. Salesforce token exchange (RFC 8693)")
    cid, csec = lines("~/.config/sprawl-scanner/salesforce_connected_app")
    status, _, text = http(f"{SF}/services/oauth2/token", {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": kc_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "client_id": cid, "client_secret": csec})
    if status != 200:
        sys.exit(f"token exchange failed: HTTP {status} {text[:500]}")
    ex = json.loads(text)
    sf_token = ex["access_token"]
    print({k: ex.get(k) for k in ("scope", "instance_url", "token_type", "issued_token_type")})

    step("3. Who is this token? (userinfo)")
    status, _, text = http(f"{SF}/services/oauth2/userinfo", headers={"Authorization": f"Bearer {sf_token}"})
    info = json.loads(text) if status == 200 else {"error": text[:300]}
    print({k: info.get(k) for k in ("name", "preferred_username", "user_id", "error")})

    run_h360(H360_DIRECT, sf_token, "direct, exchanged Salesforce token")


def run_h360(url, token, how):
    step(f"4. H360 MCP as that user ({how})")
    mcp = Mcp(url, token)
    init = mcp.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "h360-obo-test", "version": "1.0.0"}})
    print("server:", init.get("result", {}).get("serverInfo"))
    mcp.send("notifications/initialized", notify=True)

    me = mcp.tool("dispatch_readonly", {"url": "/services/data/v66.0/chatter/users/me", "method": "GET"})
    body = me.get("body") or {}
    print("running as ->", me.get("status_code"), body.get("name"), body.get("username"))

    q = ("SELECT Name, Amount, Owner.Name FROM Opportunity WHERE Amount != null "
         "ORDER BY Amount DESC LIMIT 3")
    res = mcp.tool("dispatch_readonly", {"url": "/services/data/v66.0/query", "method": "GET",
                                         "queryParams": {"q": q}})
    print("read top 3 ->", res.get("status_code"))
    for r in (res.get("body") or {}).get("records", []):
        print(f"   {r['Name']:<34} {r['Amount']:>12,.0f}  owner {r['Owner']['Name']}")

    res = mcp.tool("dispatch", {"url": f"/services/data/v66.0/sobjects/Opportunity/{Q4_DEMO_DEAL}",
                                "method": "PATCH", "body": {"Amount": 3000000}})
    print("no-op edit of Q4 Demo Deal (owned by someone else) ->", res.get("status_code"),
          json.dumps(res.get("body"))[:200])


if __name__ == "__main__":
    main()
