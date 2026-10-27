# Try it: Salesforce Headless 360, on behalf of the signed-in user

**5 minutes, nothing to install.** You log in, ask Salesforce for the top opportunities, and
try to edit them. Salesforce runs every call **as you**, so what you're allowed to do depends
on who you logged in as.

**Demo app:** https://h360-obo-demo-ky9yvn.aqopru.usa-e1.cloudhub.io

You'll get two logins from the person who shared this guide (passwords are not in this document):

| Login | Who you are in Salesforce | What to expect |
|---|---|---|
| `h360-demo-user` | **H360 Demo User**, a sales rep (Standard User) | can read every opportunity, can edit only its own |
| `h360-demo-manager` | **H360 Demo Manager**, a sales manager (Standard User + edit-all on Opportunities) | can read and edit every opportunity |

Neither login is a Salesforce admin.

---

## Steps

1. Open the demo app and click **Log in with Keycloak**. Sign in as **`h360-demo-user`**.
2. Click **Top 3 opportunities**. Note the line *"Salesforce ran this query as H360 Demo User"*.
3. Click **Try edit** on **Q4 Demo Deal** (owned by Chiew Lee) → ✖ **Refused**: *Insufficient Privileges*.
4. Click **Try edit** on **OBO Demo - Created via Keycloak** (owned by H360 Demo User) → ✔ **Allowed**.
5. *Optional:* **Create opportunity** with your own name and an amount. The result shows
   **Created By / Owner = H360 Demo User**, and it appears in the top 3 if it's big enough.
   Use **Delete** on it afterwards.
6. Click **Log out**, then log in as **`h360-demo-manager`**.
7. **Top 3 opportunities** → now *"ran this query as H360 Demo Manager"*. **Try edit** on the
   Q4 Demo Deal → ✔ **Allowed**.

*Try edit* writes the same amount back, so it never changes the data.

---

## What you just saw

```
You ── log in ──▶ Keycloak ── your token ──▶ Mule app ──▶ MuleSoft Omni Gateway
                                                           1. checks the token (JWT Validation)
                                                           2. exchanges it at Salesforce for a token
                                                              for the same person (OBO, RFC 8693)
                                                       ──▶ Salesforce Headless 360 MCP server
                                                           runs the call as you: your profile,
                                                           permission sets, sharing, field security
```

- **Same app, same calls, different results**: the only thing that changed between the two
  runs was who logged in.
- **No shared integration user**: the app never holds a Salesforce credential; records you
  create show *you* as Created By.
- **Least privilege by default**: Salesforce enforces each person's own permissions on every call.

Architecture, setup and source: https://github.com/chiewklee/h360-obo

---

## Good to know

- The demo runs in a Salesforce **demo org**; the data is sample data.
- Please only **Delete** opportunities you created yourself in the demo.
- If you see *"Your login session expired"*, click **Log out** and log in again.
