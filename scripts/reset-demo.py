#!/usr/bin/env python3
"""Reset the H360 OBO demo data in Salesforce.

  - deletes Opportunities created from the demo page (Description starts with
    "Created live in the H360 OBO demo"), so the top 3 is back to the seed deals
  - puts the seed deals' amounts back (people may edit them during the demo)

Dry run by default; pass --apply to change anything.
Calls go through the H360 MCP server with the External Client App's client-credentials token
(scope mcp_api only, so the plain REST API is not available). The app's run-as user must be
allowed to delete other people's Opportunities, e.g. a System Administrator:
  ~/.config/sprawl-scanner/salesforce_connected_app   line 1 client id, line 2 client secret
Usage: python3 scripts/reset-demo.py [--apply]
Standard library only.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SF = "https://trailsignup-ef2c09d411b9d5.my.salesforce.com"
H360_URL = "https://api.salesforce.com/platform/mcp/v1/platform/headless-360"
API = "/services/data/v66.0"
DEMO_MARKER = "Created live in the H360 OBO demo"
SEED_AMOUNTS = {  # Opportunity Id -> amount it should have
    "006gL00000WhibFQAR": 3000000,   # Q4 Demo Deal (owner Chiew Lee)
    "006gL00000YV8TxQAL": 1000000,   # OBO Demo - Created via Keycloak (owner H360 Demo User)
}


def http(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, {}, e.read().decode()


class H360:
    """Minimal MCP Streamable HTTP client for the H360 tools."""

    def __init__(self, token):
        self.token, self.session, self.n = token, None, 0
        self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "h360-obo-reset", "version": "1.0.0"}})
        self.send("notifications/initialized", notify=True)

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self.n += 1
            msg["id"] = self.n
        h = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self.session:
            h["mcp-session-id"] = self.session
        status, headers, text = http(H360_URL, json.dumps(msg).encode(), h, "POST")
        self.session = self.session or headers.get("mcp-session-id")
        if notify:
            return None
        if status >= 400:
            sys.exit(f"H360 {method}: HTTP {status} {text[:300]}")
        if not text.lstrip().startswith("{"):
            text = next(l[5:].strip() for l in text.splitlines() if l.startswith("data:"))
        return json.loads(text)

    def tool(self, name, args):
        res = self.send("tools/call", {"name": name, "arguments": args})["result"]
        return json.loads(res["content"][0]["text"])

    def query(self, soql):
        res = self.tool("dispatch_readonly", {"url": API + "/query", "method": "GET", "queryParams": {"q": soql}})
        if res.get("status_code") != 200:
            sys.exit(f"query failed: {res}")
        return res["body"]["records"]


def main():
    apply = "--apply" in sys.argv
    with open(os.path.expanduser("~/.config/sprawl-scanner/salesforce_connected_app")) as f:
        cid, csec = [l.strip() for l in f.read().splitlines()[:2]]
    status, _, text = http(SF + "/services/oauth2/token", urllib.parse.urlencode(
        {"grant_type": "client_credentials", "client_id": cid, "client_secret": csec}).encode(),
        {"Content-Type": "application/x-www-form-urlencoded"}, "POST")
    if status != 200:
        sys.exit(f"Salesforce token failed: HTTP {status} {text[:300]}")
    h360 = H360(json.loads(text)["access_token"])

    # Description is a long text field, which SOQL can't filter on: filter here instead.
    recs = h360.query("SELECT Id, Name, Amount, Description, CreatedBy.Name FROM Opportunity "
                      "WHERE CreatedDate = LAST_N_DAYS:365")
    created = [r for r in recs
               if (r["Description"] or "").startswith(DEMO_MARKER) and r["Id"] not in SEED_AMOUNTS]
    print(f"{len(created)} opportunit{'y' if len(created) == 1 else 'ies'} created from the demo page:")
    for r in created:
        print(f"  delete  {r['Name']:<36} {r['Amount'] or 0:>12,.0f}  created by {r['CreatedBy']['Name']}")
        if apply:
            res = h360.tool("dispatch", {"url": f"{API}/sobjects/Opportunity/{r['Id']}", "method": "DELETE"})
            print(f"          -> {'deleted' if res.get('status_code') in (200, 204) else res}")

    ids = "','".join(SEED_AMOUNTS)
    for r in h360.query(f"SELECT Id, Name, Amount FROM Opportunity WHERE Id IN ('{ids}')"):
        want = SEED_AMOUNTS[r["Id"]]
        if r["Amount"] == want:
            print(f"  ok      {r['Name']:<36} {r['Amount']:>12,.0f}")
            continue
        print(f"  reset   {r['Name']:<36} {r['Amount'] or 0:>12,.0f} -> {want:,.0f}")
        if apply:
            res = h360.tool("dispatch", {"url": f"{API}/sobjects/Opportunity/{r['Id']}", "method": "PATCH",
                                         "body": {"Amount": want}})
            print(f"          -> {'done' if res.get('status_code') in (200, 204) else res}")

    if not apply:
        print("\ndry run: nothing changed (pass --apply to reset)")


if __name__ == "__main__":
    main()
