# CustomsAI — Deployment Guide

Production deploy on a $0/month free-tier stack. **Read this top to bottom once** before doing anything; every step has a manual prerequisite.

---

## 1. Target architecture

```
                          ┌──────────────────────────────┐
                          │  Cloudflare (DNS + TLS + WAF)│
                          └──────────────┬───────────────┘
                       app.example.com   │   api.example.com
                              │          │          │
                ┌─────────────▼──────┐   │   ┌──────▼────────────────────┐
                │  Vercel (Hobby)    │   │   │  Oracle Cloud Always Free │
                │  Next.js frontend  │───┼──▶│  Ampere ARM 4 OCPU 24 GB  │
                │  app.example.com   │       │  nginx → FastAPI :8000    │
                └────────────────────┘       │  + worker container       │
                                             └──────────────┬────────────┘
                                                            │
                          ┌─────────────────────────────────┼──────────────────────────┐
                          ▼                                 ▼                          ▼
                  ┌───────────────┐              ┌─────────────────┐         ┌─────────────────┐
                  │  Neon         │              │  Upstash Redis  │         │  Cloudflare R2  │
                  │  Postgres     │              │  queue+progress │         │  Excel storage  │
                  │  (free)       │              │  (free)         │         │  (free 10 GB)   │
                  └───────────────┘              └─────────────────┘         └─────────────────┘
                          │
                          ▼
                  ┌───────────────┐                     ┌──────────────────┐
                  │  Anthropic    │                     │  Google OAuth    │
                  │  API (paid)   │                     │  Identity Svcs   │
                  └───────────────┘                     └──────────────────┘
```

| Layer | Service | Free tier | Notes |
|---|---|---|---|
| Frontend | Vercel Hobby | 100 GB bw/mo | Native Next.js |
| Backend | Oracle Cloud Ampere A1 | 4 OCPU / 24 GB / 200 GB | Only free option that fits the ML stack |
| Database | Neon | 0.5 GB / scale-to-zero | Pooled URL recommended |
| Queue+cache | Upstash Redis | 500K cmd/mo | Coarse progress writes |
| Object storage | Cloudflare R2 | 10 GB / 1M reads/mo | Optional v1 (VM disk works) |
| DNS+TLS+WAF | Cloudflare Free | unlimited | Wildcard cert, rate-limit rules |
| Auth | Google OAuth | free | Identity Services credential flow |
| Errors | Sentry | 5K events/mo | DSN per project |
| Uptime | UptimeRobot | 50 monitors free | 5-min ping keeps process warm |
| LLM | Anthropic | **paid**, ~$5–15 / 12K rows | Cap at $50/mo in console |

Steady-state RAM in production: ~2 GB. Peak during a 12K-row job: ~3–4 GB. Comfortably fits Ampere A1.

---

## 2. Phase B — Provision managed services (manual, ~30 minutes)

You must do these by hand. Save every credential into a password manager as you go — they are required in step 4.

### 2.1 GitHub
Push the repo to GitHub (Vercel imports from there). Make the repo private if you've ever committed real importer data; verify nothing under `data/uploads/`, `data/input_data.xlsx`, or `var/` is tracked (the `.gitignore` excludes them).

### 2.2 Neon (Postgres)
1. Sign in → **Create Project** → choose region close to your Oracle region (Mumbai/Frankfurt/US-East).
2. Copy the **Pooled connection string** (the one with `-pooler` in the host). Append `?sslmode=require` if not present.
3. Save as `DATABASE_URL=postgresql+psycopg2://...`.
4. In Neon dashboard → **Settings** → **Connections** → set **Compute autoscaling** minimum to 0 (free).

### 2.3 Upstash Redis
1. **Create Database** → name `customsai` → region matching Oracle → Type: **Regional**.
2. Copy `Redis URL` (starts with `rediss://`). Save as `REDIS_URL`.
3. Eviction: `volatile-lru`.

### 2.4 Cloudflare (DNS + R2)
1. Add your domain to Cloudflare (change nameservers at registrar).
2. **DNS** → add records:
   - `app` → CNAME → `cname.vercel-dns.com` (Proxy ON)
   - `api` → A → *Oracle public IP, set after step 3* (Proxy ON)
3. **SSL/TLS** → Encryption mode: **Full (strict)**.
4. **Security → WAF → Rate limiting rules**: create rule
   - Field: `URI Path` equals `/api/auth/google`
   - Limit: `10 requests / 1 minute / IP`
   - Action: Block 1 minute
5. **R2 → Create bucket** `customsai-uploads`. **Manage R2 API tokens** → create scoped token with R/W on this bucket. Save:
   - `R2_ENDPOINT=https://<accountid>.r2.cloudflarestorage.com`
   - `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`
   - `R2_BUCKET=customsai-uploads`

### 2.5 Google OAuth
1. Google Cloud Console → **APIs & Services → OAuth consent screen** → **External**, fill required fields, add your email as test user.
2. **Credentials → Create Credentials → OAuth client ID** → type **Web application**.
3. Authorized JavaScript origins:
   ```
   https://app.example.com
   http://localhost:3000          # for local testing
   ```
4. Authorized redirect URIs (Identity Services credential flow doesn't strictly need them, but add):
   ```
   https://app.example.com/login
   https://app.example.com
   ```
5. Save `GOOGLE_OAUTH_CLIENT_ID` (the ID, **not** the client secret — the credential-flow used here doesn't need the secret).

### 2.6 Anthropic
1. Console → **API Keys → Create Key**.
2. **Settings → Spend limits → Set monthly limit** (recommend **$50**). This is the only safety net against runaway costs; do not skip it.
3. Save `ANTHROPIC_API_KEY=sk-ant-...`.

### 2.7 Sentry (optional but recommended)
1. Create two projects: `customsai-backend` (Python/FastAPI), `customsai-frontend` (Next.js).
2. Save both DSNs as `SENTRY_DSN` and `NEXT_PUBLIC_SENTRY_DSN`.

### 2.8 Vercel
Account-only setup; nothing to do here yet. Will import the repo in step 5.

### 2.9 UptimeRobot
Account-only; you'll add the `/api/health` monitor after step 3 finishes.

### 2.10 JWT secret
Generate one now. Save it: `JWT_SECRET=<output of `openssl rand -hex 32`>`.

---

## 3. Phase C — Deploy backend to Oracle Cloud (~45 minutes)

### 3.1 Provision the VM
1. Oracle Cloud → **Compute → Instances → Create**.
   - Image: **Canonical Ubuntu 22.04 (aarch64)**
   - Shape: **VM.Standard.A1.Flex** with **4 OCPU / 24 GB**
   - Boot volume: 100 GB (free up to 200 GB total)
   - Add SSH public key.

   **Capacity caveat**: Ampere A1 is rationed. If "Out of capacity" appears, retry every few minutes and try alternate regions (Mumbai → Hyderabad → Singapore → Tokyo).
2. **Networking → VCN → Security List → Ingress Rules**:
   - Allow `22/tcp` from your IP only.
   - Allow `80/tcp`, `443/tcp` from `0.0.0.0/0`.
   - **Do NOT** open `8000/tcp` publicly.
3. Note the public IP. Update Cloudflare DNS `api` A record to point at it.

### 3.2 OS prep
SSH in as `ubuntu`:

```bash
sudo apt update && sudo apt -y upgrade
sudo apt install -y docker.io docker-compose-plugin git nginx certbot python3-certbot-nginx ufw
sudo usermod -aG docker $USER && newgrp docker
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
# Oracle Ubuntu by default also has iptables rules — flush the broad block:
sudo iptables -I INPUT 1 -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

### 3.3 Clone and configure
```bash
git clone https://github.com/<you>/customsAI.git
cd customsAI
cp backend/.env.example backend/.env
nano backend/.env
```

Set these in `backend/.env` (every other key has a sensible default):

```env
APP_ENV=production
ANTHROPIC_API_KEY=sk-ant-...
LLM_PROVIDER=claude
GOOGLE_OAUTH_CLIENT_ID=...apps.googleusercontent.com
JWT_SECRET=<your 32+ hex chars>
JWT_EXPIRE_DAYS=1
DATABASE_URL=postgresql+psycopg2://...neon.tech/neondb?sslmode=require
AUTO_MIGRATE=1
REDIS_URL=rediss://...upstash.io:6379
ALLOWED_ORIGINS=https://app.example.com
SENTRY_DSN=https://...sentry.io/...
```

`docker-compose.yml` already binds the API to `127.0.0.1:8000` — only nginx will reach it.

### 3.4 Build the enriched dataset
This must be done once on the VM (or built locally and rsync'd). Takes ~10 min.

```bash
docker compose build backend
docker compose run --rm backend python -m backend.app.scripts.build_enriched_dataset --skip-existing
docker compose run --rm backend python -m backend.app.scripts.mine_audit_data
docker compose run --rm backend python -m backend.app.scripts.build_precedent_corpus
docker compose run --rm backend python -m backend.scripts.run_validation
```

If `data/Tariff/` and `data/notifications/` aren't in the repo, scp them from your dev machine:

```bash
# from local
scp -r data/Tariff data/notifications data/'unenriched custada files' ubuntu@<oracle-ip>:~/customsAI/data/
```

### 3.5 Bring services up
```bash
docker compose up -d --build
docker compose logs -f backend
# wait for "models warm" — first cold start takes 60-180s
curl -fsS http://127.0.0.1:8000/api/health
# expect: {"status":"ok","enriched_master":true,...}
```

### 3.6 nginx + Let's Encrypt
```bash
sudo nano /etc/nginx/sites-available/customsai
```

```nginx
server {
    listen 80;
    server_name api.example.com;

    client_max_body_size 12M;
    proxy_read_timeout 120s;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   X-Real-IP $remote_addr;

        # SSE — disable buffering, allow long-lived
        proxy_buffering        off;
        proxy_cache            off;
        proxy_set_header       Connection '';
        chunked_transfer_encoding off;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/customsai /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d api.example.com  # follow prompts
sudo systemctl enable --now certbot.timer  # auto-renew
```

In Cloudflare, **flip the orange cloud** for the `api` record on. Set SSL/TLS to **Full (strict)** so Cloudflare validates the cert from Oracle.

### 3.7 Auto-restart on reboot
```bash
sudo tee /etc/systemd/system/customsai.service > /dev/null <<'EOF'
[Unit]
Description=CustomsAI compose stack
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/ubuntu/customsAI
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable customsai.service
```

### 3.8 UptimeRobot warmup ping
Add an HTTP monitor on `https://api.example.com/api/health` every **5 minutes**. This keeps the model singletons hot in memory and surfaces outages.

---

## 4. Phase D — Deploy frontend to Vercel (~10 minutes)

1. **vercel.com → Add New → Project → Import** your GitHub repo.
2. **Root Directory**: `frontend`.
3. **Framework Preset**: Next.js (auto-detected).
4. **Build Command**: `next build` (default).
5. **Environment Variables** (Production + Preview):
   ```
   NEXT_PUBLIC_BACKEND_URL = https://api.example.com
   NEXT_PUBLIC_SENTRY_DSN  = https://...sentry.io/... (optional)
   ```
6. **Deploy**.
7. **Domains → Add** `app.example.com`. Vercel verifies via the CNAME you set in Cloudflare. If the CNAME is proxied (orange cloud), Vercel will tell you to either temporarily un-proxy it or use a `_vercel` TXT record — follow Vercel's exact instructions.
8. After deploy, on backend, ensure `ALLOWED_ORIGINS=https://app.example.com` matches exactly (no trailing slash). Restart: `docker compose restart backend`.

---

## 5. Phase E — Go-live verification

Run through this checklist before announcing the URL:

```text
[ ] curl https://api.example.com/api/health → 200, "enriched_master":true, "auth_enforced":true
[ ] Open https://app.example.com → Google sign-in prompt appears, auth completes, redirected to /upload
[ ] Top bar shows "Backend online"
[ ] Upload a 100-row test xlsx → progress bar advances → completes → table renders
[ ] Click a row → detail drawer opens with reasoning + candidates
[ ] Open chat drawer → ask "explain this row" → SSE tokens stream in
[ ] Close chat tab mid-stream → backend log shows abort, no further LLM calls
[ ] Click "Re-evaluate" on a row → row updates with new classification
[ ] Click "Download annotated Excel" → file downloads, opens in Excel
[ ] Try filename "../../etc/passwd.xlsx" via curl → 400/422
[ ] Try a 50 MB xlsx → rejected (10 MB cap)
[ ] curl -i https://api.example.com/api/feedback (no auth) → 401
[ ] curl -i https://api.example.com/api/uploads/abc123 (no auth) → 401
[ ] Restart backend mid-job (`docker compose restart backend`) → that job marked "failed: interrupted by restart" within 1 minute
[ ] Open SSE chat → leave idle 90s → connection alive (heartbeat)
[ ] Anthropic console → spend tracking matches expected cost for 100 rows
[ ] Sentry dashboard → no errors after 24h pilot run
[ ] UptimeRobot → /api/health monitor green
[ ] DNS: dig app.example.com / api.example.com → both resolve correctly
[ ] Browser dev tools: no CORS errors, CSP allows everything legit, HSTS header present
```

---

## 6. What to do if Ampere A1 capacity is unavailable

If you cannot get the 4 OCPU / 24 GB shape after a week of retries:

**Option A — smaller Ampere shape**: The 1 OCPU / 6 GB shape is usually available. Set in `backend/.env`:
```env
LLM_CONCURRENCY=2
BATCH_SIZE=10
```
The pipeline is lexical-only (no embedding/reranker models to load), so this drops memory use to ~1.2 GB at idle, ~2 GB peak. Slower but works.

**Option B — Hugging Face Spaces**: Free 16 GB Space (CPU). Sleep-on-idle. Fork the repo, add a `Dockerfile.hf` that wraps the backend, push as a Space. CORS still works — frontend stays on Vercel.

**Option C — Google Cloud Run**: 8 GB RAM, 4 vCPU available; pay only for usage. Cold start every scale-to-zero is 60-90s — not really free, but generous free tier credits.

---

## 7. Daily operational tasks

| Task | Command | Frequency |
|---|---|---|
| View live logs | `docker compose logs -f backend` | as needed |
| Restart backend | `sudo systemctl restart customsai` | after env changes |
| Pull latest code | `cd ~/customsAI && git pull && docker compose build && docker compose up -d` | per release |
| Check disk usage | `df -h` | weekly |
| Prune old images | `docker system prune -f` | monthly |
| Renew TLS | automatic via `certbot.timer` | n/a |
| Apply DB migrations | `docker compose run --rm backend python -m alembic -c backend/alembic.ini upgrade head` | per release that ships migrations |

---

## 8. Manual configuration summary

Everything you must set up by hand before going live (consolidated):

| Item | Where | Saved as |
|---|---|---|
| Domain registered | any registrar | — |
| DNS at Cloudflare | Cloudflare dashboard | — |
| Oracle account + Ampere A1 instance | Oracle Cloud | SSH key, public IP |
| Neon Postgres pooled URL | Neon dashboard | `DATABASE_URL` |
| Upstash Redis URL | Upstash dashboard | `REDIS_URL` |
| Cloudflare R2 bucket + token | Cloudflare R2 | `R2_*` (4 vars) |
| Google OAuth Web Client ID | Google Cloud Console | `GOOGLE_OAUTH_CLIENT_ID` |
| Anthropic API key + spend cap | Anthropic Console | `ANTHROPIC_API_KEY` |
| Sentry projects (×2) | Sentry | `SENTRY_DSN`, `NEXT_PUBLIC_SENTRY_DSN` |
| JWT secret | `openssl rand -hex 32` | `JWT_SECRET` |
| Vercel project | Vercel dashboard | `NEXT_PUBLIC_BACKEND_URL`, `NEXT_PUBLIC_SENTRY_DSN` |
| `ALLOWED_ORIGINS` | `backend/.env` on Oracle VM | `https://app.example.com` |
| nginx site + Let's Encrypt cert | Oracle VM | — |
| systemd unit | Oracle VM | `customsai.service` |
| UptimeRobot HTTP monitor | UptimeRobot | every 5 min on `/api/health` |
| Cloudflare WAF rate-limit rule | Cloudflare Security | `/api/auth/google` 10/min/IP |

If any of these is missing, the corresponding feature breaks (auth fails open, jobs lost on restart, no rate limit, etc.). Do not skip steps.

---

## 9. Phase F — Post-launch hardening (after first week of pilot)

Items deliberately deferred so you can ship sooner:

- **Replace `BackgroundTasks` with RQ + dedicated worker**. Use Upstash Redis as broker; resume `running` jobs on worker startup.
- **Move Excel storage from VM disk → R2**. Currently kept under `var/uploads/{job_id}/...` on Oracle disk. R2 SDK keys are already in `backend/.env.example` for when you wire it.
- **Add `/api/me/delete` endpoint** for DPDP / GDPR data-subject deletion.
- **Add per-user daily LLM spend cap** in `backend/app/services/pipeline.py` (track tokens in DB, hard-stop at threshold).
- **Add `output: "standalone"` + `output-tracing` review** for further Vercel cold-start reduction.
- **Strip importer name / IEC / BE No before sending row prompts to LLM**. Currently the prompt only includes description + declared CTH/BCD, but double-check `pipeline.py:188` if you've added new fields.

---

## 10. Smoke-test the deployed app

After step 5 verification passes, run a real 12K-row workbook through it. Time it. Anthropic console will show actual token spend; compare to your monthly cap. If you see runaway cost or repeated 429s, lower `LLM_CONCURRENCY` and `BATCH_SIZE`.

Done. Total monthly cost: $0 infra + variable Anthropic spend (capped).
