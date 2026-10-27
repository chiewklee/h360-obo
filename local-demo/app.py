#!/usr/bin/env python3
"""H360 On-Behalf-Of demo web app.

  1. "Log in with Keycloak": OIDC authorization code + PKCE against realm h360-obo.
  2. "Top 3 opportunities": calls the Salesforce Headless 360 MCP server through the Omni
     gateway OBO route with the user's Keycloak access token. The gateway validates it
     (JWT Validation) and exchanges it for a Salesforce token for the same person (OBO
     policy), so Salesforce runs the query as that user, with that user's permissions.
  3. "Try to edit": a no-op edit of the largest deal; refused unless the user can edit it.

The browser only ever holds a session cookie; tokens stay in this process's memory.
Run:  python3 local-local-demo/app.py   then open http://localhost:8790
Standard library only.
"""

import base64
import hashlib
import html
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8790"))
BASE_URL = f"http://localhost:{PORT}"
REALM = "https://keycloak-1792182140.web.cloud-services.demos.mulesoft.com/realms/h360-obo"
OIDC = REALM + "/protocol/openid-connect"
CLIENT_ID = "h360-mcp-client"
GATEWAY = os.environ.get(
    "H360_OBO_URL", "https://my-flex-gateway-ky9yvn.aqopru.usa-e1.cloudhub.io/sfdc-headless-360-obo/")
API = "/services/data/v66.0"

SESSIONS = {}  # session id -> {"state", "verifier", "tokens", "claims"}


# ---------------------------------------------------------------- HTTP helpers

def http(url, data=None, headers=None, method=None):
    body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode()


def jwt_claims(token):
    # Received straight from Keycloak's token endpoint over TLS, so decoding for display is
    # enough here; the gateway does the real signature validation on every H360 call.
    p = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


# ---------------------------------------------------------------- MCP client

class H360Error(Exception):
    pass


class Mcp:
    """Minimal MCP Streamable HTTP client: initialize, then tools/call."""

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
        self.session = self.session or headers.get("mcp-session-id")
        if notify:
            return None
        if status >= 400:
            raise H360Error(f"gateway/H360 HTTP {status}: {text[:300]}")
        if not text.lstrip().startswith("{"):  # SSE framing
            text = next(l[5:].strip() for l in text.splitlines() if l.startswith("data:"))
        return json.loads(text)

    def open(self):
        self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "h360-obo-demo", "version": "1.0.0"}})
        self.send("notifications/initialized", notify=True)
        return self

    def tool(self, name, args):
        res = self.send("tools/call", {"name": name, "arguments": args})
        if "error" in res:
            raise H360Error(res["error"].get("message", "MCP error"))
        content = res["result"].get("content") or []
        return json.loads(content[0]["text"]) if content else res["result"]

    def get(self, path, query=None):
        args = {"url": API + path, "method": "GET"}
        if query:
            args["queryParams"] = query
        return self.tool("dispatch_readonly", args)


def running_as(mcp):
    me = mcp.get("/chatter/users/me")
    b = me.get("body") or {}
    return {"name": b.get("name"), "username": b.get("username")}


def top_opportunities(token):
    mcp = Mcp(GATEWAY, token).open()
    q = ("SELECT Id, Name, Account.Name, Amount, StageName, CloseDate, Owner.Name "
         "FROM Opportunity WHERE Amount != null ORDER BY Amount DESC LIMIT 3")
    res = mcp.get("/query", {"q": q})
    if res.get("status_code") != 200:
        raise H360Error(f"query failed: {json.dumps(res.get('body'))[:300]}")
    rows = [{"id": r["Id"], "name": r["Name"], "account": (r.get("Account") or {}).get("Name"),
             "amount": r["Amount"], "stage": r["StageName"], "close": r["CloseDate"],
             "owner": (r.get("Owner") or {}).get("Name")} for r in res["body"]["records"]]
    return {"runningAs": running_as(mcp), "rows": rows}


def try_edit(token, opp_id):
    """No-op edit: write back the current Amount, so nothing changes even if it is allowed."""
    mcp = Mcp(GATEWAY, token).open()
    cur = mcp.get("/query", {"q": f"SELECT Name, Amount, Owner.Name FROM Opportunity WHERE Id = '{opp_id}'"})
    rec = cur["body"]["records"][0]
    res = mcp.tool("dispatch", {"url": f"{API}/sobjects/Opportunity/{opp_id}", "method": "PATCH",
                                "body": {"Amount": rec["Amount"]}})
    ok = res.get("status_code") in (200, 204)
    detail = "" if ok else sf_error(res.get("body"))
    return {"runningAs": running_as(mcp), "opportunity": rec["Name"], "owner": rec["Owner"]["Name"],
            "allowed": ok, "status": res.get("status_code"), "detail": str(detail).split("\n")[0]}


def sf_error(body):
    if isinstance(body, str) and body.startswith("["):
        body = json.loads(body)
    if isinstance(body, list) and body:
        return body[0].get("message", str(body)).split("\n")[0]
    return str(body)


# H360 applies the page layout's required fields, so Account, Currency and Forecast
# category are sent too. Stages come from the Master record type's sales process.
DEMO_ACCOUNT = "001gL00001ZIzoMQAT"  # Acme


def create_opportunity(token, name, amount):
    mcp = Mcp(GATEWAY, token).open()
    res = mcp.tool("dispatch", {"url": f"{API}/sobjects/Opportunity", "method": "POST", "body": {
        "Name": name, "Amount": amount, "AccountId": DEMO_ACCOUNT, "StageName": "Qualification",
        "ForecastCategoryName": "Pipeline", "CurrencyIsoCode": "USD", "CloseDate": "2026-12-31",
        "Description": "Created live in the H360 OBO demo (Keycloak login -> Omni Gateway OBO -> H360 MCP)"}})
    body = res.get("body")
    if not (isinstance(body, dict) and body.get("id")):
        raise H360Error(f"create refused: {sf_error(body)}")
    r = mcp.get("/query", {"q": "SELECT Name, Amount, Account.Name, Owner.Name, CreatedBy.Name, CreatedDate "
                                 f"FROM Opportunity WHERE Id = '{body['id']}'"})["body"]["records"][0]
    return {"runningAs": running_as(mcp), "id": body["id"], "name": r["Name"], "amount": r["Amount"],
            "account": r["Account"]["Name"], "owner": r["Owner"]["Name"], "createdBy": r["CreatedBy"]["Name"]}


def delete_opportunity(token, opp_id):
    mcp = Mcp(GATEWAY, token).open()
    res = mcp.tool("dispatch", {"url": f"{API}/sobjects/Opportunity/{opp_id}", "method": "DELETE"})
    ok = res.get("status_code") in (200, 204)
    return {"allowed": ok, "detail": "" if ok else sf_error(res.get("body"))}


# ---------------------------------------------------------------- web app

class App(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

    # -- session cookie
    def session(self, create=False):
        sid = SimpleCookie(self.headers.get("Cookie", "")).get("sid")
        sid = sid.value if sid else None
        if sid in SESSIONS:
            return sid, SESSIONS[sid]
        if not create:
            return None, None
        sid = secrets.token_urlsafe(24)
        SESSIONS[sid] = {}
        return sid, SESSIONS[sid]

    def send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, url, cookie=None):
        h = {"Location": url}
        if cookie:
            h["Set-Cookie"] = f"sid={cookie}; HttpOnly; SameSite=Lax; Path=/"
        self.send(302, "", headers=h)

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj), "application/json")

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        route = {"/": self.page, "/login": self.login, "/callback": self.callback,
                 "/logout": self.logout, "/api/top": self.api_top}.get(url.path)
        if route:
            route(q)
        else:
            self.send(404, "not found", "text/plain")

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        actions = {"/api/edit": lambda t, b: try_edit(t, b.get("id", "")),
                   "/api/create": lambda t, b: create_opportunity(
                       t, (b.get("name") or "OBO Demo - Live").strip()[:120], float(b.get("amount") or 1000000)),
                   "/api/delete": lambda t, b: delete_opportunity(t, b.get("id", ""))}
        action = actions.get(url.path)
        if not action:
            return self.send(404, "not found", "text/plain")
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        tok = self.token_or_401()
        if tok:
            try:
                self.json(action(tok, body))
            except (H360Error, ValueError, KeyError, IndexError) as e:
                self.json({"error": str(e)}, 502)

    # -- OIDC authorization code + PKCE
    def login(self, _):
        sid, s = self.session(create=True)
        s["state"] = secrets.token_urlsafe(16)
        s["verifier"] = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(s["verifier"].encode()).digest()).rstrip(b"=").decode()
        params = {"client_id": CLIENT_ID, "response_type": "code", "scope": "openid profile email",
                  "redirect_uri": BASE_URL + "/callback", "state": s["state"],
                  "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "login"}
        self.redirect(OIDC + "/auth?" + urllib.parse.urlencode(params), cookie=sid)

    def callback(self, q):
        sid, s = self.session()
        if not s or q.get("state") != s.get("state"):
            return self.send(400, "Login state mismatch. <a href='/'>Start again</a>.")
        if "error" in q:
            return self.send(400, f"Keycloak said: {html.escape(q['error'])}. <a href='/'>Back</a>.")
        status, _, text = http(OIDC + "/token", {
            "grant_type": "authorization_code", "client_id": CLIENT_ID, "code": q.get("code", ""),
            "redirect_uri": BASE_URL + "/callback", "code_verifier": s.pop("verifier", "")})
        if status != 200:
            return self.send(502, f"Token request failed: {html.escape(text[:300])}")
        s["tokens"] = json.loads(text)
        s["claims"] = jwt_claims(s["tokens"]["access_token"])
        self.redirect("/")

    def logout(self, _):
        sid, s = self.session()
        id_token = (s or {}).get("tokens", {}).get("id_token")
        SESSIONS.pop(sid, None)
        params = {"client_id": CLIENT_ID, "post_logout_redirect_uri": BASE_URL + "/"}
        if id_token:
            params["id_token_hint"] = id_token
        self.redirect(OIDC + "/logout?" + urllib.parse.urlencode(params))

    # -- API for the page
    def token_or_401(self):
        _, s = self.session()
        tok = (s or {}).get("tokens", {}).get("access_token")
        if not tok:
            self.json({"error": "Not logged in"}, 401)
        return tok

    def api_top(self, _):
        tok = self.token_or_401()
        if tok:
            try:
                self.json(top_opportunities(tok))
            except H360Error as e:
                self.json({"error": str(e)}, 502)

    # -- page
    def page(self, _):
        _, s = self.session()
        c = (s or {}).get("claims")
        if c:
            who = (f"<div class='who'><span class='ok'>✔ Signed in</span> <b>{html.escape(c.get('name') or c.get('preferred_username', ''))}</b>"
                   f" <span class='muted'>({html.escape(c.get('preferred_username', ''))} · Keycloak realm h360-obo)</span>"
                   f" <a class='btn ghost' href='/logout'>Log out</a></div>")
            body = PAGE.replace("{{WHO}}", who).replace("{{HIDDEN}}", "")
        else:
            who = ("<div class='who'><span class='muted'>Not signed in.</span>"
                   " <a class='btn' href='/login'>Log in with Keycloak</a></div>")
            body = PAGE.replace("{{WHO}}", who).replace("{{HIDDEN}}", "hidden")
        self.send(200, body)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>H360 On-Behalf-Of demo</title>
<style>
 body{font-family:"Salesforce Sans",Arial,sans-serif;margin:0;background:#F3F3F3;color:#181818}
 header{background:#032D60;color:#fff;padding:18px 32px}
 header h1{margin:0;font-size:22px} header p{margin:4px 0 0;color:#1B96FF;font-size:14px}
 main{max-width:1000px;margin:24px auto;padding:0 24px}
 .card{background:#fff;border:1px solid #C9C9C9;border-radius:8px;padding:20px;margin-bottom:16px}
 .flow{font-size:13px;color:#444;line-height:1.6} .flow b{color:#014486}
 .btn{display:inline-block;background:#0176D3;color:#fff;border:0;border-radius:4px;padding:8px 16px;font-size:14px;text-decoration:none;cursor:pointer;margin-left:8px}
 .btn.ghost{background:#fff;color:#0176D3;border:1px solid #0176D3}
 .btn:disabled{opacity:.5;cursor:wait}
 .muted{color:#747474} .ok{color:#2E844A;font-weight:bold} .bad{color:#EA001E;font-weight:bold}
 table{width:100%;border-collapse:collapse;margin-top:12px;font-size:14px}
 th{text-align:left;color:#444;border-bottom:2px solid #C9C9C9;padding:8px}
 td{border-bottom:1px solid #F3F3F3;padding:8px} td.num{text-align:right;font-variant-numeric:tabular-nums}
 .runas{margin-top:12px;padding:10px 12px;background:#F3F3F3;border-left:4px solid #0176D3;font-size:14px}
 .hidden{display:none}
 input{font:inherit;padding:6px 8px;border:1px solid #C9C9C9;border-radius:4px}
 .btn.small{padding:4px 10px;font-size:12px;margin-left:4px}
 td .res{display:block;font-size:12px;margin-top:4px}
</style></head><body>
<header><h1>Salesforce Headless 360, on behalf of the signed-in user</h1>
<p>Keycloak login → Omni Gateway (JWT Validation + OBO token exchange) → H360 MCP → Salesforce</p></header>
<main>
 <div class="card">{{WHO}}</div>
 <div class="card {{HIDDEN}}" id="actions">
  <button class="btn" id="top" onclick="loadTop()">Top 3 opportunities</button>
  <div id="out"></div>
 </div>
 <div class="card {{HIDDEN}}">
  <b style="color:#014486">Create an opportunity as yourself</b>
  <div style="margin-top:10px">
   <input id="oname" value="OBO Demo - Live" size="32">
   <input id="oamt" value="1000000" size="12" style="text-align:right"> USD
   <button class="btn" id="create" onclick="createOpp()">Create opportunity</button>
   <span class="muted">Account Acme · stage Qualification · close 2026-12-31</span>
  </div>
  <div id="createout"></div>
 </div>
 <div class="card flow">
  <b>What happens on each click:</b> the app sends your <b>Keycloak</b> access token to the Omni Gateway.
  The gateway checks its signature, audience and expiry, then exchanges it at Salesforce for a token
  <b>for the same person</b> (RFC 8693, Apex token-exchange handler maps your <code>sf_username</code> claim).
  H360 runs the query with <b>your</b> Salesforce permissions. No Salesforce credentials reach this app.
 </div>
</main>
<script>
const fmt = n => n.toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0});
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const post = (url, body) => fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  .then(async r => { const d = await r.json(); if (!r.ok) throw new Error(d.error); return d; });
let me = null;
async function loadTop(){
  const b = document.getElementById('top'), out = document.getElementById('out');
  b.disabled = true; out.innerHTML = '<p class="muted">Calling H360 through the gateway…</p>';
  try {
    const r = await fetch('/api/top'); const d = await r.json();
    if (!r.ok) throw new Error(d.error);
    me = d.runningAs.name;
    out.innerHTML = `<div class="runas">Salesforce ran this query as <b>${esc(me)}</b>
       <span class="muted">(${esc(d.runningAs.username)})</span></div>
      <table><tr><th>#</th><th>Opportunity</th><th>Account</th><th style="text-align:right">Amount</th><th>Stage</th><th>Owner</th><th>Actions</th></tr>
      ${d.rows.map((o,i)=>`<tr><td>${i+1}</td><td>${esc(o.name)}</td><td>${esc(o.account)}</td><td class="num">${fmt(o.amount)}</td>
        <td>${esc(o.stage)}</td><td>${o.owner===me ? '<b>'+esc(o.owner)+'</b> (you)' : esc(o.owner)}</td>
        <td><button class="btn ghost small" onclick="tryEdit(this,'${o.id}')">Try edit</button>
            ${o.owner===me ? `<button class="btn ghost small" onclick="del(this,'${o.id}','${esc(o.name)}')">Delete</button>` : ''}
            <span class="res"></span></td></tr>`).join('')}</table>
      <p class="muted">"Try edit" writes the same amount back, so nothing changes even when it is allowed.</p>`;
  } catch(e){ out.innerHTML = `<p class="bad">✖ ${esc(e.message)}</p>`; }
  b.disabled = false;
}
async function tryEdit(btn, id){
  const res = btn.parentElement.querySelector('.res'); btn.disabled = true; res.innerHTML = '<span class="muted">…</span>';
  try {
    const d = await post('/api/edit', {id});
    res.innerHTML = d.allowed ? '<span class="ok">✔ Allowed</span>'
      : `<span class="bad">✖ Refused</span> <span class="muted">${esc(d.detail)}</span>`;
  } catch(e){ res.innerHTML = `<span class="bad">✖ ${esc(e.message)}</span>`; }
  btn.disabled = false;
}
async function del(btn, id, name){
  if (!confirm(`Delete "${name}" in Salesforce?`)) return;
  const res = btn.parentElement.querySelector('.res'); btn.disabled = true;
  try {
    const d = await post('/api/delete', {id});
    if (!d.allowed) { res.innerHTML = `<span class="bad">✖ Refused</span> <span class="muted">${esc(d.detail)}</span>`; btn.disabled = false; return; }
    loadTop();
  } catch(e){ res.innerHTML = `<span class="bad">✖ ${esc(e.message)}</span>`; btn.disabled = false; }
}
async function createOpp(){
  const b = document.getElementById('create'), out = document.getElementById('createout');
  b.disabled = true; out.innerHTML = '<p class="muted">Creating through the gateway…</p>';
  try {
    const d = await post('/api/create', {name: document.getElementById('oname').value,
                                         amount: document.getElementById('oamt').value.replace(/[^0-9.]/g,'')});
    out.innerHTML = `<div class="runas"><span class="ok">✔ Created</span> "${esc(d.name)}" ${fmt(d.amount)} on ${esc(d.account)}.
      Created By <b>${esc(d.createdBy)}</b>, Owner <b>${esc(d.owner)}</b>. <span class="muted">(${esc(d.id)})</span></div>`;
    loadTop();
  } catch(e){ out.innerHTML = `<p class="bad">✖ ${esc(e.message)}</p>`; }
  b.disabled = false;
}
</script></body></html>"""


if __name__ == "__main__":
    print(f"H360 OBO demo on {BASE_URL}  (gateway {GATEWAY})")
    ThreadingHTTPServer(("127.0.0.1", PORT), App).serve_forever()
