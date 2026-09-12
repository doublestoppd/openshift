# Open Shift Response Portal

A deliberately small Django web application that replaces manually texting
nurses and CNAs about open shifts.

An authorized department administrator (e.g. CNO, ER Director) posts an open
shift, picks staff or staff groups, and sends each person a personalized SMS
link. Staff tap the link and answer in seconds: **full shift**, **part of the
shift** (with from/until times), or **can't work it**. The administrator sees
all responses in one place and gets a text when someone accepts. Actual
scheduling decisions happen outside this application.

**This is not a scheduling system.** There are no slots, assignments,
approvals, payroll, or patient data.

- Stack: Python 3 · Django · SQLite · server-rendered templates · Twilio ·
  gunicorn · Caddy · one Ubuntu DigitalOcean Droplet.
- Default timezone: `America/Chicago` (shifts expire automatically after
  their calendar date passes in this timezone).

---

## 1. Local development setup

Requirements: Python 3.11+.

```bash
git clone <this repository>
cd openshift
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

Edit `.env` for development:

```
DJANGO_DEBUG=true
SMS_BACKEND=console        # prints SMS to the terminal instead of using Twilio
PORTAL_BASE_URL=http://localhost:8000
```

Then:

```bash
python manage.py migrate
PORTAL_ADMIN_PASSWORD='choose-a-long-password' \
    python manage.py create_portal_admin cno \
    --departments MED_SURG ER --notification-phone "312-555-0100"
python manage.py runserver
```

- Administrator UI: <http://localhost:8000/manage/>
- Staff manual login: <http://localhost:8000/staff/login/>
- With `SMS_BACKEND=console`, every "sent" SMS (including each staff
  member's personal invitation link) is printed to the runserver console —
  copy the link into a browser to act as that staff member.

Run the test suite:

```bash
python manage.py test portal
```

## 2. Creating administrators

Administrators are created from the command line (there is intentionally no
self-signup and no admin-management UI):

```bash
PORTAL_ADMIN_PASSWORD='...' python manage.py create_portal_admin <username> \
    --departments ER MED_SURG \
    --notification-phone "312-555-0100" \
    --name "Alex Rivera"
```

- `--departments` takes one or both of `ER`, `MED_SURG` and controls exactly
  which departments that account can create/manage shifts for.
- `--notification-phone` is where acceptance texts for shifts *created by
  this administrator* are sent. It can be changed later on the Account page.
- Password comes from the `PORTAL_ADMIN_PASSWORD` environment variable (or an
  interactive prompt if unset). Passwords are validated and stored using
  Django's standard hashing; nothing is hardcoded.
- Running the command again for an existing username updates departments /
  phone (and password only if the environment variable is set). Ask
  administrators to change the bootstrap password on the Account page
  afterwards.

Typical initial setup:

```bash
PORTAL_ADMIN_PASSWORD='...' python manage.py create_portal_admin cno --departments MED_SURG ER
PORTAL_ADMIN_PASSWORD='...' python manage.py create_portal_admin er_director --departments ER
```

## 3. Twilio configuration

1. In the Twilio console, note the **Account SID**, an **Auth Token**, and an
   SMS-capable **phone number**.
2. Set in the environment file (never in source control):

   ```
   SMS_BACKEND=twilio
   TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxx
   TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxx
   TWILIO_FROM_NUMBER=+1312xxxxxxx
   ```

3. `PORTAL_BASE_URL` must be the public HTTPS URL of the site — it is used to
   build the invitation links in messages.

Notes:

- Sending is synchronous; the send screen reports exactly how many messages
  were submitted to Twilio and which failed. Failures for one recipient never
  stop the rest.
- Every outbound message (invitations and administrator acceptance alerts) is
  recorded on the **SMS Log** page with the Twilio message SID or the error.
- Delivery-status webhooks are intentionally not implemented; the log shows
  submission results only.

## 4. Quick start on a fresh DigitalOcean Droplet (development / testing)

This section stands up a complete, throwaway test instance on one small
Droplet — with a real domain, real Let's Encrypt HTTPS, and real SMS — in
about 30 minutes. Caddy terminates HTTPS exactly as in production; behind
it the app runs Django's development server with `DEBUG` on, so this is
still **not** the production setup. Use it to evaluate the workflow with
test data, then follow section 5 for a real deployment. Never load real
employee names or phone numbers into a test instance, and destroy the
Droplet when finished.

You need a domain you control. The steps below assume it was purchased
through GoDaddy and use `shifts.example.com` as the test hostname —
substitute your own subdomain (e.g. `shifts.yourdomain.com`) everywhere it
appears.

### 4.1 Create the Droplet

1. DigitalOcean → **Create → Droplets**.
2. Image: **Ubuntu 24.04 (LTS)**. Size: the smallest **Basic** plan
   (512 MB–1 GB is plenty for testing).
3. Authentication: your SSH key.
4. Create it, then note its public IP address — written as `DROPLET_IP`
   in everything below.

```bash
ssh root@DROPLET_IP
```

### 4.2 Point a subdomain at the Droplet (GoDaddy)

Do this first so DNS has time to propagate while you install. Using a
subdomain (`shifts.yourdomain.com`) leaves the domain's existing website
and email records untouched.

1. Sign in at godaddy.com → **My Products** → next to the domain, open
   **DNS** (sometimes labeled **Manage DNS**).
2. **Add New Record**:
   - **Type:** A
   - **Name:** `shifts` — just the subdomain part; GoDaddy appends the
     domain automatically. (To use the bare domain itself, put `@` here
     instead, provided nothing else is hosted on it.)
   - **Value:** `DROPLET_IP`
   - **TTL:** the lowest offered (e.g. 600 seconds / ½ hour), so later
     changes take effect quickly.
3. Save. If a record with that name already exists, edit it rather than
   adding a duplicate.

Verify before moving on — Let's Encrypt can only issue a certificate once
the name actually resolves to this Droplet:

```bash
dig +short shifts.example.com
# must print DROPLET_IP — repeat every few minutes until it does
```

GoDaddy changes usually appear within minutes, occasionally up to an hour.

> Alternative: you can instead switch the domain's nameservers to
> DigitalOcean (`ns1`/`ns2`/`ns3.digitalocean.com`) and manage records in
> the DO control panel — but that moves *all* DNS for the domain and takes
> longer to propagate. For a test box, the single GoDaddy A record above
> is simpler.

### 4.3 Install the application

On the Droplet:

```bash
apt update && apt install -y python3-venv python3-pip git sqlite3 tmux

git clone https://github.com/doublestoppd/openshift.git
cd openshift
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

(If the repository is private, create a GitHub personal access token with
read access and clone with
`git clone https://<token>@github.com/doublestoppd/openshift.git`.)

Configure the environment:

```bash
cp .env.example .env
nano .env
```

Set these values, using your real hostname:

```
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=shifts.example.com
PORTAL_BASE_URL=https://shifts.example.com
TIME_ZONE=America/Chicago
SMS_BACKEND=console
```

`PORTAL_BASE_URL` matters even in testing: it is the address the texted
invitation links point at, so it must be the public `https://` hostname
you just configured.

Initialize the database, create a test administrator, and open the web
ports — 80 is needed for the HTTP→HTTPS redirect and Let's Encrypt
validation, 443 for HTTPS itself:

```bash
python manage.py migrate
python manage.py test portal      # optional sanity check — all tests should pass

PORTAL_ADMIN_PASSWORD='pick-a-long-test-password' \
    python manage.py create_portal_admin cno \
    --departments MED_SURG ER --notification-phone "YOUR-MOBILE-NUMBER"

ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
```

Use your own mobile number for `--notification-phone` — that is where
acceptance alerts will be texted during the test. (If you attached a
DigitalOcean Cloud Firewall to the Droplet, also open TCP 80 and 443
there; `ufw` alone is enough otherwise.)

### 4.4 Caddy + Let's Encrypt (HTTPS)

Install Caddy from its official repository:

```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
```

Replace the contents of `/etc/caddy/Caddyfile` with just:

```
shifts.example.com {
	reverse_proxy 127.0.0.1:8000
}
```

Then:

```bash
systemctl reload caddy
journalctl -u caddy --no-pager -n 20    # watch it obtain the certificate
```

That is the entire HTTPS setup. As soon as the config loads, Caddy
contacts Let's Encrypt, proves control of the hostname (this is why DNS
had to resolve first and why port 80 must be open), installs the
certificate, redirects all HTTP to HTTPS, and renews automatically from
then on.

If issuance fails, `journalctl -u caddy` says why — it is almost always
DNS not yet pointing at this Droplet, or port 80/443 blocked. One caution
for repeated tear-down/rebuild cycles: Let's Encrypt issues at most 5
identical certificates per hostname per week, so reuse the same Droplet
within a test cycle rather than recreating it several times a day (or
vary the subdomain name).

Browsing to `https://shifts.example.com` right now shows a Caddy 502
error page — expected, since the application isn't running yet.

### 4.5 Run it — console SMS first, no Twilio needed yet

Start the development server inside `tmux` so it survives SSH disconnects.
It binds to localhost only — Caddy is the sole internet-facing process:

```bash
tmux new -s portal
cd ~/openshift && source .venv/bin/activate
python manage.py runserver 127.0.0.1:8000
```

Detach with `Ctrl-B` then `D`; reattach later with `tmux attach -t portal`.

From your computer or phone, open `https://shifts.example.com/manage/` —
note the padlock — and sign in as `cno`. With `SMS_BACKEND=console`
nothing touches Twilio: every
"sent" SMS is printed in the runserver terminal instead, including each
staff member's personal invitation link. Copy a link into your phone's
browser to play the staff role end to end before spending any Twilio
credit.

### 4.6 Twilio setup for real test texts

A free Twilio trial account is enough for testing:

1. Sign up at <https://www.twilio.com/try-twilio> and verify your own
   mobile number during signup.
2. In the Twilio Console, choose **Get a trial phone number** and accept
   the suggested US number (it must have SMS capability). This becomes the
   portal's sending number.
3. Trial-account limits to know about:
   - Twilio delivers only to **verified** numbers. Verify every phone you
     will test with under **Phone Numbers → Manage → Verified Caller IDs**
     (your signup number is already verified).
   - Every trial message is prefixed with "Sent from your Twilio trial
     account". Upgrading the account (adding a payment method) removes
     both limits.
   - For ongoing production texting from a US local number, Twilio also
     requires A2P 10DLC registration (Console → Messaging → Regulatory
     Compliance). That is not needed for low-volume trial texts to
     verified numbers, but plan for it before go-live.
4. From the Console dashboard, copy the **Account SID** and **Auth Token**.
5. Update `.env` on the Droplet:

   ```
   SMS_BACKEND=twilio
   TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   TWILIO_AUTH_TOKEN=your-auth-token
   TWILIO_FROM_NUMBER=+1XXXXXXXXXX
   ```

6. Restart the dev server (`Ctrl-C` in the tmux session, then run
   `python manage.py runserver 127.0.0.1:8000` again) — `.env` is read at
   startup.

### 4.7 End-to-end test walkthrough

With Twilio configured:

1. **Staff** → **+ Add staff**: add yourself — any badge ID, your verified
   mobile number, role RN. Add a second tester if you have another
   verified phone.
2. **Groups** → **+ New group**: e.g. "Test RNs", with your test staff as
   members.
3. **Open Shifts** → **+ New Open Shift**: pick a department, tomorrow's
   date, Night Shift, role RN, a $150 incentive → **Save & Choose
   Recipients**.
4. Select the group (watch the unique-recipient counter), **Continue**,
   and confirm the send.
5. Your phone receives the invitation text. Tap the link and respond
   **I can work part of the shift** with from/until times.
6. The administrator's notification phone receives the acceptance text,
   and the dashboard card now shows `1 partial`.
7. Re-open the same link and change the response, then check **View
   Responses** and the **SMS Log** page. Failed sends show Twilio's error
   message in the log — e.g. error 21608 means the destination number is
   not verified on your trial account.
8. Try the manual path too: `https://shifts.example.com/staff/login/` with
   the badge ID + last 4 digits of the mobile number.

### 4.8 Tearing down

Even with HTTPS, a test instance still runs with `DEBUG` on — never leave
it up unattended and never put real employee data in it. When finished:

1. Destroy the Droplet (**Destroy → Destroy Droplet**), which also stops
   billing.
2. Delete (or repoint) the `shifts` A record in GoDaddy DNS. The Let's
   Encrypt certificate needs no cleanup — it simply expires.

Or keep the Droplet and promote it properly by following section 5: the
domain, DNS record, firewall, and Caddyfile you just set up are exactly
what production uses — what changes is swapping the dev server for
gunicorn under systemd, setting `DJANGO_DEBUG=false`, and moving to the
hardened user/directory layout.

## 5. Production deployment on DigitalOcean

One Ubuntu LTS Droplet (1 GB is plenty). Commands below assume a sudo-capable
login; adjust paths if you prefer a different layout.

```
/srv/openshift-portal/
├── app/      # this repository
├── venv/     # Python virtual environment
├── data/     # db.sqlite3 (writable by the service)
└── backups/  # nightly database backups
```

### 5.1 System preparation

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip sqlite3 unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades   # enable automatic security updates

# Dedicated non-root user for the application
sudo adduser --system --group --home /srv/openshift-portal shiftportal

sudo mkdir -p /srv/openshift-portal/{app,data,backups}
sudo chown -R shiftportal:shiftportal /srv/openshift-portal
```

Use SSH key authentication for your login and disable password SSH auth in
`/etc/ssh/sshd_config` (`PasswordAuthentication no`).

### 5.2 Application install

```bash
sudo -u shiftportal -H bash
cd /srv/openshift-portal
git clone <this repository> app
python3 -m venv venv
venv/bin/pip install -r app/requirements.txt
exit
```

### 5.3 Environment file (secrets)

Create `/etc/openshift-portal/env`, owned by root, readable by the service
only:

```bash
sudo mkdir -p /etc/openshift-portal
sudo touch /etc/openshift-portal/env
sudo chown root:shiftportal /etc/openshift-portal/env
sudo chmod 640 /etc/openshift-portal/env
```

Contents (see `.env.example` for the full list):

```
DJANGO_SECRET_KEY=<output of: python3 -c "import secrets; print(secrets.token_urlsafe(50))">
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=shifts.example.org
PORTAL_BASE_URL=https://shifts.example.org
TIME_ZONE=America/Chicago
DATABASE_PATH=/srv/openshift-portal/data/db.sqlite3
SMS_BACKEND=twilio
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+1...
```

### 5.4 Migrate, collect static files, create administrators

```bash
sudo -u shiftportal -H bash
cd /srv/openshift-portal/app
set -a; source /etc/openshift-portal/env; set +a
../venv/bin/python manage.py migrate
../venv/bin/python manage.py collectstatic --noinput
PORTAL_ADMIN_PASSWORD='<initial password>' ../venv/bin/python manage.py \
    create_portal_admin cno --departments MED_SURG ER --notification-phone "..."
exit
```

(Static files are served by the app itself via WhiteNoise, so Caddy needs no
static-file configuration.)

### 5.5 gunicorn under systemd

```bash
sudo cp /srv/openshift-portal/app/deploy/openshift-portal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openshift-portal
systemctl status openshift-portal
```

The unit binds gunicorn to `127.0.0.1:8000` only — it is never exposed to the
internet directly.

### 5.6 Caddy (HTTPS)

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

sudo cp /srv/openshift-portal/app/deploy/Caddyfile /etc/caddy/Caddyfile
# edit /etc/caddy/Caddyfile: replace shifts.example.org with your hostname
sudo systemctl reload caddy
```

Point the DNS A record of your hostname at the Droplet **before** starting
Caddy (for a GoDaddy-purchased domain, the record setup is described in
section 4.2); Caddy then obtains and renews the TLS certificate
automatically and redirects all HTTP requests to HTTPS. The site is
HTTPS-only.

### 5.7 DigitalOcean Cloud Firewall

Create a Cloud Firewall attached to the Droplet with **inbound** rules:

| Type  | Port | Source                                  |
|-------|------|------------------------------------------|
| TCP   | 80   | All IPv4/IPv6 (HTTP→HTTPS redirect + ACME) |
| TCP   | 443  | All IPv4/IPv6                            |
| TCP   | 22   | Your administrator IP(s) only, if practical |

Everything else stays closed. SQLite is a local file and gunicorn listens on
localhost, so neither is reachable from outside regardless.

### 5.8 Nightly database backup

```bash
sudo -u shiftportal crontab -e
# add:
15 2 * * * /srv/openshift-portal/app/deploy/backup.sh >> /srv/openshift-portal/backups/backup.log 2>&1
```

The script uses SQLite's online `.backup` (safe while the app runs), gzips
the copy into `/srv/openshift-portal/backups/`, and prunes copies older than
14 days. Also keep a copy of `/etc/openshift-portal/env` somewhere safe
(password manager) — it holds the secret key and Twilio credentials.

**Restore procedure:**

```bash
sudo systemctl stop openshift-portal
gunzip -k /srv/openshift-portal/backups/db-<stamp>.sqlite3.gz
sudo -u shiftportal cp /srv/openshift-portal/backups/db-<stamp>.sqlite3 \
    /srv/openshift-portal/data/db.sqlite3
sudo systemctl start openshift-portal
```

### 5.9 Updating the application

```bash
sudo -u shiftportal -H bash -c '
cd /srv/openshift-portal/app && git pull &&
../venv/bin/pip install -r requirements.txt &&
set -a; source /etc/openshift-portal/env; set +a;
../venv/bin/python manage.py migrate &&
../venv/bin/python manage.py collectstatic --noinput'
sudo systemctl restart openshift-portal
```

## 6. How it works (operator summary)

- **Administrators** sign in at `/manage/` with username/password. Each
  account is limited server-side to its assigned department(s). Navigation:
  Open Shifts | Staff | Groups | SMS Log | History | Account.
- **Creating a shift** never sends SMS. Sending happens only from the
  recipient screen after an explicit confirmation showing the deduplicated
  recipient count.
- **Staff** get a personal link (`/i/<token>`). The token is a long random
  value; only its SHA-256 hash is stored, and the URL contains no names,
  badge IDs, or phone numbers. Staff without the link can sign in at
  `/staff/login/` with badge ID + last 4 digits of their mobile number.
- **Responses**: full / partial (from–until times) / decline. Staff can
  change their answer while the shift is open; only the current answer is
  kept. Full or partial acceptances (and material changes to them) text the
  shift's creator; declines never do.
- **Shifts expire** automatically once their calendar date has passed
  (checked whenever shift pages load — no cron needed), and administrators
  can close a shift manually at any time. Closed/expired shifts still show
  their info to staff but accept no responses, and remain visible under
  History.
- **Rate limiting**: staff and administrator logins are limited to 8 failed
  attempts per 15 minutes per IP and per badge/username (configurable via
  environment variables).

## 7. Implementation assumptions

Decisions made where the specification left room:

1. **Resending rotates the invitation token.** The spec asks both to store
   only a hash of the token and to reuse the token on resend; these
   conflict, so hash-only storage won (it protects links if the database
   leaks). A resend reuses the same invitation row — so existing responses
   and the one-invitation-per-staff/shift rule are preserved — but issues a
   fresh link, and the previously texted link for **that person** stops
   working the moment a new one is sent to them. If Twilio rejects the
   resend, the previous link is restored. Links of staff not included in a
   resend are unaffected.
2. **US phone numbers only.** Numbers are normalized to E.164 `+1XXXXXXXXXX`;
   10-digit entries are assumed to be US numbers.
3. **Shift dates cannot be in the past** when creating or editing a shift
   (they would be born expired).
4. **Past-date open shifts are marked EXPIRED lazily** — whenever a
   dashboard, history, responses, or staff page loads — instead of by a
   scheduled job. The response-acceptance check also validates the date
   directly, so a stale status can never let a response through.
5. **Administrator accounts are created via management command only** (no
   admin-management UI, no Django admin site is enabled), matching the
   "initial credentials from a deployment command" requirement and keeping
   the attack surface small.
6. **Staff manual-login sessions last 30 minutes** (configurable via
   `STAFF_SESSION_AGE`).
7. **A "material change" that re-notifies the creator** is: first acceptance,
   decline→acceptance, switching between full and partial, or changing the
   partial time range. Re-submitting an identical answer does not re-text.
8. **The staff directory and groups are shared** by all administrators (per
   spec §4.1); department permissions apply to shifts only.
9. **Inactive staff opening their old link** get a generic "This link is no
   longer active." page (HTTP 410) that reveals nothing about the shift or
   the employee record.
10. **`incentive_amount` is capped at 99,999.99** by the decimal field
    definition; amounts display without trailing `.00`.

## 8. Repository layout

```
config/            Django project (settings, urls, wsgi)
portal/            The single application
  models.py        AdminProfile, Staff, StaffGroup, Shift, Invitation,
                   ShiftResponse, SmsLog, RateLimitEvent
  manage_views.py  Administrator screens
  staff_views.py   Staff screens (token + badge login)
  sms.py           Message building, Twilio/console transport, SMS log
  responses.py     Response saving + acceptance-notification rules
  tokens.py        Invitation token generation/hashing
  phones.py        E.164 normalization
  ratelimit.py     DB-backed login rate limiting
  templates/       Server-rendered pages
  static/          One small stylesheet
  tests/           Test suite (47 tests)
deploy/            Caddyfile, systemd unit, backup script
```
