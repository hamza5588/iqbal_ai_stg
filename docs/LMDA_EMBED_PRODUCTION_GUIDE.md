# LMDA Embed Chatbot — Production Guide

Complete step-by-step guide to run the Iqbal AI **B2B embed consultant** in production for **LMDA** (`https://lmda.com.pk`), with the API hosted at:

**`http://209.23.13.199/`** (no custom domain yet)

---

## Table of contents

1. [What you are deploying](#1-what-you-are-deploying)
2. [Architecture](#2-architecture)
3. [Prerequisites](#3-prerequisites)
4. [Production server setup](#4-production-server-setup)
5. [Environment variables (production)](#5-environment-variables-production)
6. [Start / restart the application](#6-start--restart-the-application)
7. [Step A — Upload the LMDA PDF (one time)](#7-step-a--upload-the-lmda-pdf-one-time)
8. [Step B — Bind PDF to embed client `lmda`](#8-step-b--bind-pdf-to-embed-client-lmda)
9. [Step C — Verify API health](#9-step-c--verify-api-health)
10. [Step D — End-to-end test before client handoff](#10-step-d--end-to-end-test-before-client-handoff)
11. [Step E — Give LMDA the embed snippet](#11-step-e--give-lmda-the-embed-snippet)
12. [Owner emails (escalation + export)](#12-owner-emails-escalation--export)
13. [Voice chat notes (HTTPS)](#13-voice-chat-notes-https)
14. [Admin & maintenance](#14-admin--maintenance)
15. [Troubleshooting](#15-troubleshooting)
16. [Quick reference](#16-quick-reference)

---

## 1. What you are deploying

| Who | What they do |
|-----|----------------|
| **You (Iqbal AI)** | Host API, upload LMDA’s PDF once, configure embed client, give LMDA a script tag |
| **LMDA website** | Paste embed script on `lmda.com.pk` — no backend work |
| **Website visitors** | Chat (text/voice) with AI trained on LMDA’s document |
| **LMDA owner** | Receives email when a visitor shares contact info; can request CSV export of all chats |

**Visitors do not upload PDFs.** You upload the document once; all visitors share it.

---

## 2. Architecture

```
┌─────────────────────┐         ┌──────────────────────────────┐
│  lmda.com.pk        │  HTTPS  │  http://209.23.13.199        │
│  (client website)   │ ──────► │  Iqbal AI API + embed widget │
│  embed script only  │  API    │  PostgreSQL + Chroma RAG     │
└─────────────────────┘         └──────────────────────────────┘
         │                                    │
         │ visitors chat                      │ escalation / export
         ▼                                    ▼
   embed_conversations              owner email (e.g. info@lmda.com.pk)
   embed_messages (per visitor)
```

**Public embed APIs** (no login; authenticated with `X-Client-Key`):

| Endpoint | Purpose |
|----------|---------|
| `POST /api/consultant/public/session` | Start visitor session |
| `POST /api/consultant/public/chat` | Text chat + RAG |
| `POST /api/consultant/public/callback` | Callback form → owner email |
| `POST /api/consultant/public/export-chats` | Email all chats CSV to owner |
| `POST /api/consultant/public/voice/connect` | Voice WebRTC handshake |
| `GET /api/consultant/public/voice/health` | Check OpenAI Realtime key |
| `POST /api/consultant/public/tool` | Voice document search tools |

**Demo page (for your testing):** `http://209.23.13.199/embed/demo`

---

## 3. Prerequisites

On server **`209.23.13.199`**:

- [ ] Python 3.10+ and project venv (`/path/to/iqbal_ai_consultant/venv`)
- [ ] PostgreSQL running with app database
- [ ] `.env` configured (see below)
- [ ] Port **80** (or reverse proxy) serving the Flask app
- [ ] Outbound internet (Groq/OpenAI for chat, OpenAI for voice, SMTP for email)
- [ ] LMDA PDF already ingested or ready to upload

**LMDA values (current):**

| Item | Value |
|------|--------|
| Client slug | `lmda` |
| Client website | `https://lmda.com.pk` |
| Client key | `1UNApiFwpeUKDiTsnUPynqvZr-6B3Ve4eHAzYGPRj5E` |
| Document `thread_id` | `user_2_conv_101_1782716154_ab9f2db8` |
| Document `service_user_id` | `2` |
| Test owner email | `moonkhanswati3@gmail.com` |
| Production owner email | `info@lmda.com.pk` |

---

## 4. Production server setup

### 4.1 Deploy code

```bash
# On server 209.23.13.199
cd /path/to/iqbal_ai_consultant
git pull   # or copy latest iqbal_ai_stg branch
source venv/bin/activate
cd iqbal_ai_stg
pip install -r requirements.txt
```

### 4.2 Process manager (recommended)

Use **gunicorn** + **nginx** (not `python run.py` in production):

```bash
# Example gunicorn (adjust workers/path)
cd iqbal_ai_stg
gunicorn -w 4 -b 127.0.0.1:5002 "run:app" --timeout 120
```

**Nginx** example (port 80 → app):

```nginx
server {
    listen 80;
    server_name 209.23.13.199;

    location / {
        proxy_pass http://127.0.0.1:5002;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Reload nginx: `sudo nginx -t && sudo systemctl reload nginx`

> If you already serve the app on port 80 without nginx, set `API_BASE` in docs to `http://209.23.13.199` (no port).

---

## 5. Environment variables (production)

Edit **`iqbal_ai_stg/.env`** on the server:

```bash
# ── App ─────────────────────────────────────────────
ENV=production
SERVER_URL=http://209.23.13.199
SECRET_KEY=<long-random-secret>

# ── Database (your production values) ─────────────
# DATABASE_URL=postgresql://user:pass@localhost:5432/mydatabase

# ── Email (required for owner alerts + CSV export) ─
MAIL_USERNAME=info@iqbalai.com
MAIL_PASSWORD=<your-smtp-password>

# ── LLM (text chat uses Groq by default) ──────────
GROQ_API_KEY=<your-groq-key>

# ── OpenAI (voice only) ───────────────────────────
OPENAI_API_KEY=<your-openai-key>
OPENAI_REALTIME_MODEL=gpt-realtime
OPENAI_REALTIME_VOICE=marin
OPENAI_REALTIME_USE_ENV_KEY=true

# ── Embed / LMDA ──────────────────────────────────
EMBED_CLIENT_KEYS=lmda:1UNApiFwpeUKDiTsnUPynqvZr-6B3Ve4eHAzYGPRj5E
EMBED_DEFAULT_OWNER_EMAIL=info@lmda.com.pk
ALLOWED_ORIGINS=http://209.23.13.199,https://consultation.iqbalai.com,https://lmda.com.pk,https://www.lmda.com.pk,https://tnmco.uk,https://www.tnmco.uk

# Optional: rate limit per visitor IP per hour (default 60)
# EMBED_RATE_LIMIT_PER_HOUR=120
```

**Important:**

- `ALLOWED_ORIGINS` must include **exact** origins the browser sends (`https://lmda.com.pk`, not `http://`).
- Add `http://209.23.13.199` so `/embed/demo` works on the IP.
- When you get a domain (e.g. `api.iqbalai.com`), update `SERVER_URL`, `ALLOWED_ORIGINS`, and the embed `apiBase`.

---

## 6. Start / restart the application

```bash
cd /path/to/iqbal_ai_consultant
source venv/bin/activate
cd iqbal_ai_stg

# Development only:
# python run.py 5002

# Production (after gunicorn/systemd setup):
sudo systemctl restart iqbal-ai   # if you created a systemd unit
```

Confirm:

```bash
curl -s -o /dev/null -w "%{http_code}" http://209.23.13.199/embed/demo
# Expect: 200
```

---

## 7. Step A — Upload the LMDA PDF (one time)

Only **you** upload the document (not LMDA visitors).

### Option 1 — Dashboard (easiest)

1. Open `http://209.23.13.199/auth/login`
2. Log in as teacher/admin (user id **2** uploaded the current document)
3. Open teacher dashboard → **Consultant** widget (bottom-right)
4. Upload LMDA PDF → wait until **document is ready**
5. Note `thread_id` from browser DevTools → Network → `POST /api/consultant/ingest` response

### Option 2 — API (curl)

```bash
# After logging in via browser, copy session cookie
curl -X POST http://209.23.13.199/api/consultant/ingest \
  -H "Cookie: session=YOUR_SESSION_COOKIE" \
  -F "file=@/path/to/lmda-document.pdf" \
  -F "session_id=lmda_doc"
```

**Current bound document (already uploaded):**

- `thread_id`: `user_2_conv_101_1782716154_ab9f2db8`
- `service_user_id`: `2`

If you upload a **new** PDF, run Step B again with the new `thread_id`.

---

## 8. Step B — Bind PDF to embed client `lmda`

On the server (use the **same venv** as the running app):

```bash
cd iqbal_ai_stg
source ../venv/bin/activate

python3 scripts/embed_onboard.py \
  --slug lmda \
  --owner-email info@lmda.com.pk \
  --rag-thread-id user_2_conv_101_1782716154_ab9f2db8 \
  --service-user-id 2 \
  --origins "https://lmda.com.pk,https://www.lmda.com.pk,https://tnmco.uk,https://www.tnmco.uk,https://consultation.iqbalai.com,http://209.23.13.199"
```

Expected output: `Updated client lmda`

**For testing** (emails to your Gmail instead of LMDA):

```bash
python3 scripts/embed_onboard.py \
  --slug lmda \
  --owner-email moonkhanswati3@gmail.com \
  --rag-thread-id user_2_conv_101_1782716154_ab9f2db8 \
  --service-user-id 2 \
  --origins "https://lmda.com.pk,https://www.lmda.com.pk,https://tnmco.uk,https://www.tnmco.uk,https://consultation.iqbalai.com,http://209.23.13.199"
```

Restart the app after onboarding.

---

## 9. Step C — Verify API health

### 9.1 OpenAI voice key (optional)

```bash
python3 scripts/check_openai_voice.py
# Expect: OK: API key is valid and Realtime client_secrets works
```

### 9.2 Embed voice health API

```bash
curl -s http://209.23.13.199/api/consultant/public/voice/health \
  -H "X-Client-Key: 1UNApiFwpeUKDiTsnUPynqvZr-6B3Ve4eHAzYGPRj5E" | python3 -m json.tool
```

Expect: `"ok": true`, `"code": "OPENAI_OK"`

### 9.3 Demo page

Open: **http://209.23.13.199/embed/demo**

---

## 10. Step D — End-to-end test before client handoff

Use this checklist on production IP:

| # | Test | How | Pass? |
|---|------|-----|-------|
| 1 | Widget loads | Open `/embed/demo`, click **Consultant** | ☐ |
| 2 | RAG answers | Ask something from LMDA PDF; DevTools → `public/chat` → `"used_rag": true` | ☐ |
| 3 | Typing indicator | Send message → dots appear until reply | ☐ |
| 4 | Escalation email | Ask to speak to owner → give email → check owner inbox | ☐ |
| 5 | Export CSV | Run curl below → owner gets `embed_chats_lmda.csv` | ☐ |
| 6 | Voice (optional) | Voice tab → Start Voice → speak (may need HTTPS on client site) | ☐ |

**Export all chats (CSV email):**

```bash
curl -X POST http://209.23.13.199/api/consultant/public/export-chats \
  -H "Content-Type: application/json" \
  -H "X-Client-Key: 1UNApiFwpeUKDiTsnUPynqvZr-6B3Ve4eHAzYGPRj5E" \
  -d '{}'
```

**Escalation flow:**

1. Visitor asks something the AI cannot answer, or asks for a human
2. Visitor provides **email or phone** in chat
3. Owner receives **one email** with that conversation transcript (no links to visitor)
4. Email is sent only **after** contact info is provided

---

## 11. Step E — Give LMDA the embed snippet

Send LMDA **only** this (paste before `</body>` on every page they want the widget):

```html
<script src="http://209.23.13.199/static/js/consultant-embed.js"></script>
<script>
  IqbalConsultant.init({
    apiBase: "http://209.23.13.199",
    clientKey: "1UNApiFwpeUKDiTsnUPynqvZr-6B3Ve4eHAzYGPRj5E"
  });
</script>
```

**LMDA does not need:**

- Your database credentials
- API keys (only `clientKey` in the snippet)
- Any server-side code

### Mixed content warning (important)

`lmda.com.pk` is **HTTPS**. Your API is **HTTP** (`209.23.13.199`).

Modern browsers may **block** HTTP scripts on HTTPS pages. For reliable production:

1. Put **HTTPS** on the API (Let’s Encrypt on a subdomain, e.g. `https://api.iqbalai.com`), **or**
2. Use a reverse proxy with SSL on the IP (less common)

Until then, test embed on:

- `http://209.23.13.199/embed/demo` (works)
- LMDA site may block the widget until API is HTTPS

**When you get a domain**, update the snippet:

```html
<script src="https://YOUR-API-DOMAIN/static/js/consultant-embed.js"></script>
<script>
  IqbalConsultant.init({
    apiBase: "https://YOUR-API-DOMAIN",
    clientKey: "1UNApiFwpeUKDiTsnUPynqvZr-6B3Ve4eHAzYGPRj5E"
  });
</script>
```

And add the new origin to `ALLOWED_ORIGINS` + re-run `embed_onboard.py --origins ...`.

---

## 12. Owner emails (escalation + export)

| Email type | When | Recipient | Content |
|------------|------|-----------|---------|
| **Escalation** | Visitor gives email/phone in chat | `owner_email` on `lmda` client | Single chat transcript + visitor contact |
| **Export CSV** | You or owner calls export API | Same owner email | `embed_chats_lmda.csv` — all conversations |

**Switch to LMDA production email:**

```bash
python3 scripts/embed_onboard.py \
  --slug lmda \
  --owner-email info@lmda.com.pk \
  --rag-thread-id user_2_conv_101_1782716154_ab9f2db8 \
  --service-user-id 2 \
  --origins "https://lmda.com.pk,https://www.lmda.com.pk,https://tnmco.uk,https://www.tnmco.uk,https://consultation.iqbalai.com,http://209.23.13.199"
```

Emails are sent via SMTP configured in `.env` (`MAIL_USERNAME` / `MAIL_PASSWORD`).

---

## 13. Voice chat notes (HTTPS)

| Requirement | Detail |
|-------------|--------|
| Microphone | Browser requires **secure context** (HTTPS page or localhost) |
| OpenAI Realtime | Valid `OPENAI_API_KEY` with Realtime quota |
| Rate limits | Avoid clicking **Start Voice** repeatedly; wait 60s if 429 |
| API check | `python3 scripts/check_openai_voice.py` |

On `http://209.23.13.199/embed/demo`, voice may work in some browsers on HTTP IP; on **HTTPS lmda.com.pk** the **API should also be HTTPS** for voice.

---

## 14. Admin & maintenance

### Admin embed clients (logged-in admin)

- `GET /admin/embed-clients` — list clients
- `POST /admin/embed-clients` — create client
- `PUT /admin/embed-clients/<id>` — update owner, origins, document binding
- `POST /admin/embed-clients/<id>/export` — email CSV to owner

### Rotate client key

1. Generate: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
2. Update `.env`: `EMBED_CLIENT_KEYS=lmda:NEW_SECRET`
3. Update DB via `embed_onboard.py` or admin UI
4. Send LMDA updated snippet with new `clientKey`
5. Restart app

### Replace LMDA document

1. Upload new PDF (Step A) → new `thread_id`
2. Run `embed_onboard.py` with new `--rag-thread-id`
3. Restart app

---

## 15. Troubleshooting

| Problem | Cause | Fix |
|---------|--------|-----|
| `Origin not allowed` | `ALLOWED_ORIGINS` missing client URL | Add `https://lmda.com.pk` to `.env` and onboard `--origins` |
| `Invalid client key` | Wrong `clientKey` in snippet | Match `.env` `EMBED_CLIENT_KEYS` |
| `used_rag: false` | Document not bound | Run `embed_onboard.py` with correct `thread_id` |
| Owner email missing visitor email | Email sent before visitor typed contact | Fixed in app — visitor must send email first; start new chat to test |
| No escalation email | SMTP misconfigured | Check `MAIL_*` in `.env`, server logs |
| `502` on voice connect | OpenAI error | Run `check_openai_voice.py`; check logs |
| `429` on voice | OpenAI rate limit | Wait 1–2 minutes; avoid repeated clicks |
| Widget blank on LMDA | Mixed content (HTTPS → HTTP API) | Enable HTTPS on API domain |
| `embed_onboard.py` import error | Wrong Python | Use project venv: `source venv/bin/activate` |

**Logs:** watch gunicorn/systemd logs or terminal where `run.py` runs.

---

## 16. Quick reference

```text
Production API:     http://209.23.13.199
Demo page:          http://209.23.13.199/embed/demo
Client website:     https://lmda.com.pk
Client slug:        lmda
Client key:         1UNApiFwpeUKDiTsnUPynqvZr-6B3Ve4eHAzYGPRj5E
thread_id:          user_2_conv_101_1782716154_ab9f2db8
service_user_id:    2
Owner (prod):       info@lmda.com.pk
Owner (test):       moonkhanswati3@gmail.com
```

**Onboard command (copy-paste on server):**

```bash
cd iqbal_ai_stg && source ../venv/bin/activate && \
python3 scripts/embed_onboard.py \
  --slug lmda \
  --owner-email info@lmda.com.pk \
  --rag-thread-id user_2_conv_101_1782716154_ab9f2db8 \
  --service-user-id 2 \
  --origins "https://lmda.com.pk,https://www.lmda.com.pk,https://tnmco.uk,https://www.tnmco.uk,https://consultation.iqbalai.com,http://209.23.13.199"
```

**Handoff checklist for LMDA:**

- [ ] Production `.env` on `209.23.13.199`
- [ ] PDF uploaded and bound (`embed_onboard.py` OK)
- [ ] Escalation email tested to `info@lmda.com.pk`
- [ ] Export CSV tested
- [ ] Embed snippet sent (or HTTPS API ready for `lmda.com.pk`)
- [ ] Text chat verified with `used_rag: true`

---

*Document version: June 2026 — Iqbal AI embed service for LMDA*


