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

## 4. Production deployment on DigitalOcean

One Ubuntu LTS Droplet (1 GB is plenty). Commands below assume a sudo-capable
login; adjust paths if you prefer a different layout.

```
/srv/openshift-portal/
├── app/      # this repository
├── venv/     # Python virtual environment
├── data/     # db.sqlite3 (writable by the service)
└── backups/  # nightly database backups
```

### 4.1 System preparation

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

### 4.2 Application install

```bash
sudo -u shiftportal -H bash
cd /srv/openshift-portal
git clone <this repository> app
python3 -m venv venv
venv/bin/pip install -r app/requirements.txt
exit
```

### 4.3 Environment file (secrets)

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

### 4.4 Migrate, collect static files, create administrators

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

### 4.5 gunicorn under systemd

```bash
sudo cp /srv/openshift-portal/app/deploy/openshift-portal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openshift-portal
systemctl status openshift-portal
```

The unit binds gunicorn to `127.0.0.1:8000` only — it is never exposed to the
internet directly.

### 4.6 Caddy (HTTPS)

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
Caddy; it then obtains and renews the TLS certificate automatically and
redirects all HTTP requests to HTTPS. The site is HTTPS-only.

### 4.7 DigitalOcean Cloud Firewall

Create a Cloud Firewall attached to the Droplet with **inbound** rules:

| Type  | Port | Source                                  |
|-------|------|------------------------------------------|
| TCP   | 80   | All IPv4/IPv6 (HTTP→HTTPS redirect + ACME) |
| TCP   | 443  | All IPv4/IPv6                            |
| TCP   | 22   | Your administrator IP(s) only, if practical |

Everything else stays closed. SQLite is a local file and gunicorn listens on
localhost, so neither is reachable from outside regardless.

### 4.8 Nightly database backup

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

### 4.9 Updating the application

```bash
sudo -u shiftportal -H bash -c '
cd /srv/openshift-portal/app && git pull &&
../venv/bin/pip install -r requirements.txt &&
set -a; source /etc/openshift-portal/env; set +a;
../venv/bin/python manage.py migrate &&
../venv/bin/python manage.py collectstatic --noinput'
sudo systemctl restart openshift-portal
```

## 5. How it works (operator summary)

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

## 6. Implementation assumptions

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

## 7. Repository layout

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
