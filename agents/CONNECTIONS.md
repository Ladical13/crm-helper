# Marketing connections — setup checklist

Read-only data connections for the Nimbus marketing agents. Status lives at
**Nimbus → 🔌 Connections** (admin only).

**Nothing in this file is a secret, and nothing secret should ever be added to
it.** Keys, tokens and service-account files go into Railway environment
variables and nowhere else — not into this repo, not into a chat window, not
into a code comment.

---

## Status at a glance

| Connection | Tier | State |
|---|---|---|
| Website / sitemap | Active | ✅ Connected, no credential needed |
| Google Business Profile | Active | Pending Google's API access approval |
| Google Search Console | **Optional — owner access required** | Franchise-owned; we hold no role |
| Google Analytics 4 | **Optional — owner access required** | Franchise-owned; we hold no role |

**Search Console and GA4 are optional future enhancements, not broken
connections.** Confirmed 2026-08-10: both consoles show an empty screen for
`luke@projectoneroofing.com`. Nimbus displays them as *Owner access required*,
muted rather than red, and no marketing work waits on them — see
[MARKETING_PLAN.md](MARKETING_PLAN.md). The setup steps below stay documented
because the day someone grants access, setting the env vars is the entire
change.

## 1. Which connections use a read-only service account

Both share **one** service account, so there is one key to create and one key
to rotate. **Both are currently owner-gated** — the service account exists and
works; nobody has been able to grant it access.

| Connection | Read-only scope | Who must grant |
|---|---|---|
| Google Analytics 4 | `analytics.readonly` | A franchise property **Administrator**. No self-serve route exists |
| Google Search Console | `webmasters.readonly` | A franchise **Owner** — or self-verify a URL-prefix property via a CMS meta tag, which needs nobody |

A service account is the right fit here: it is not tied to a person, it can be
granted view-only access inside each product, and it needs no browser consent.

## 2. Which connections require a Google OAuth login

| Connection | Why |
|---|---|
| Google Business Profile | Google's Business Profile APIs **do not support service accounts.** Access must be authorised by a Google account that manages the profile. |

Two things to know before starting GBP:

- **There is no read-only scope.** Google publishes exactly one scope for these
  APIs, `business.manage`, and it can write. Read-only for GBP is enforced by
  Nimbus — `agents/connections.py` only ever issues GETs, and a test fails the
  build if a write verb appears in it. That is a weaker guarantee than GA4's
  and GSC's, where Google itself refuses writes. Worth knowing before granting.
- **Access needs Google's approval.** Beyond enabling the APIs you must submit
  Google's Business Profile API access request form. Approval is not instant.

**Website / CMS** needs no credential at all in v1 — it reads the public
`sitemap.xml`. No CMS login, nothing to grant, nothing to revoke.

## 3. Railway environment variables

Set these in the Railway dashboard on the `project-one-estimator` service
(Variables tab). Exact names — the app reads these and no others.

### Shared service account (GA4 + Search Console)

| Variable | Contents |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON_B64` | The service-account JSON key file, base64-encoded |

`GOOGLE_SERVICE_ACCOUNT_JSON` (raw JSON) also works, but base64 is preferred:
the private key inside contains newlines, and pasting it raw into a dashboard
field is where it usually gets mangled.

Encode the key **straight to the clipboard**, so it never appears on screen or
in shell history:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("$HOME\Downloads\key.json")) | Set-Clipboard
```

Paste into Railway, then delete the downloaded `.json` file. Do not commit it,
do not move it into the repo, do not paste it into a chat.

### Per-connection IDs and credentials

| Variable | Connection | Secret? |
|---|---|---|
| `GA4_PROPERTY_ID` | Google Analytics 4 | No |
| `GSC_SITE_URL` | Search Console | No |
| `GBP_OAUTH_CLIENT_ID` | Business Profile | Yes |
| `GBP_OAUTH_CLIENT_SECRET` | Business Profile | Yes |
| `GBP_OAUTH_REFRESH_TOKEN` | Business Profile | Yes |
| `GBP_ACCOUNT_ID` | Business Profile (optional) | No |
| `GBP_LOCATION_ID` | Business Profile (optional) | No |
| `MARKETING_SITE_URL` | Website (optional) | No |

`MARKETING_SITE_URL` defaults to the website in `marketing_profile.json`, so
you only need it if the two should differ.

## 4. The non-secret IDs you need to find

These are identifiers, not credentials. The Connections page displays them back
to you so you can confirm you pasted the right value.

### `GA4_PROPERTY_ID`
GA4 → **Admin** → **Property Settings** → *Property ID*.
Digits only — `123456789`, not `properties/123456789`. (The app strips a
`properties/` prefix if you include it anyway.) This is **not** the
`G-XXXXXXX` measurement ID, which is a different thing and will not work.

### `GSC_SITE_URL`
Search Console → the property picker at top left. The format depends on the
property type and must match exactly:

- Domain property → `sc-domain:projectoneroofingcolorado.com`
- URL-prefix property → `https://projectoneroofingcolorado.com/` (**with** the
  trailing slash)

### `GBP_ACCOUNT_ID` and `GBP_LOCATION_ID`
**Both are optional, and you discover them by connecting** — which is why
neither gates the probe. Set the three OAuth variables, and either:

- run `python -m agents.scripts.gbp_oauth`, which prints both at the end; or
- hit **Re-check** on the Connections page, which reports the account IDs it
  can read in the status line.

Set `GBP_ACCOUNT_ID` only to pin reads to one account. Leave `GBP_LOCATION_ID`
unset to read every location.

If the lookup comes back HTTP 403, that is the expected state while the
Business Profile API access request is still pending — the refresh token is
fine, Google just hasn't granted quota yet.

## 5. Verifying each connection without changing live data

Every check below is a single HTTP **GET**. None of them writes, publishes, or
modifies anything.

**The easy way:** Nimbus → 🔌 Connections → **Re-check all**. Each connector
reports Connected / Not Connected / Error with a reason. That is the same set
of calls listed here.

| Connection | Verification call | Proves |
|---|---|---|
| GA4 | `GET analyticsdata.googleapis.com/v1beta/properties/{id}/metadata` | The SA can read that property |
| Search Console | `GET webmasters/v3/sites/{siteUrl}` | The SA is a user on that property; returns its permission level |
| Business Profile | `GET mybusinessaccountmanagement.googleapis.com/v1/accounts` | OAuth works and the account manages ≥1 profile |
| Website | `GET {site}/sitemap.xml` | The sitemap is reachable and parseable |

Reading the reasons:

- **`HTTP 401/403`** — authentication worked, authorisation did not. The
  credential is valid but has not been granted access to that specific
  property. Re-check the sharing step, and give Google a few minutes.
- **`HTTP 404`** — the ID is wrong. For Search Console this is almost always
  the `sc-domain:` vs `https://` format, or a missing trailing slash.
- **`HTTP 403` on Business Profile only** — often means API access has not been
  approved yet, rather than a permissions problem.

---

## Getting the GBP refresh token safely

One-time, run on your own machine. The token is printed to **your local
terminal** and goes straight into Railway — it never touches this repo or a
chat window.

1. Google Cloud Console → **APIs & Services → Credentials** → *Create
   credentials* → **OAuth client ID** → application type **Desktop app**.
2. Note the client ID and client secret (leave them in the browser for now).
3. Run the helper, which starts a local loopback listener and opens Google's
   consent screen:

```bash
python -m agents.scripts.gbp_oauth
```

4. Sign in as the Google account that manages the Business Profile and approve.
5. The script prints the refresh token to your terminal. Copy it into Railway
   as `GBP_OAUTH_REFRESH_TOKEN`, along with the client ID and secret.
6. Close the terminal. The script writes nothing to disk.

---

## Rotating or revoking

- **Service account key** — Cloud Console → the service account → *Keys* →
  add a new key, update `GOOGLE_SERVICE_ACCOUNT_JSON_B64`, redeploy, then
  delete the old key. Revoking access entirely: remove the SA's access in GA4
  and Search Console, which takes effect immediately.
- **GBP OAuth** — the granting Google account can revoke Nimbus at
  <https://myaccount.google.com/permissions>. That invalidates the refresh
  token immediately.

## Scope of v1 — what these connections may NOT do

Read-only, enforced in code and by tests in `agents/tests/test_connections.py`:

- no publishing (no Business Profile posts, no review replies)
- no email sending
- no website or CMS editing
- no CRM writes

Adding any of those is a deliberate, separate change — not a config tweak.

## Still unspecified

**Colorado marketing data.** Listed as something to connect, but the system it
lives in has not been named — spreadsheet, ad account, BI export, something
else. It appears on the Connections page as an open item so it does not get
forgotten. Name the system and it becomes a real connector.
