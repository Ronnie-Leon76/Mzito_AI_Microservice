# Zoho Cliq → Freshservice / Freddy AI middleware

A FastAPI service that gives a Zoho Cliq bot three helpdesk capabilities, backed by Freshservice:

1. Search published solution articles.
2. Create a Freshservice incident, with a short multi-turn flow if the user doesn't give a description up front.
3. Retrieve a ticket's status — restricted to the ticket's own requester.

## Why this doesn't call "Freddy AI" directly

Freshworks does not currently publish an API that lets a third-party channel like Zoho Cliq hold a conversation with Freddy AI Agent directly. Freddy AI Agent (the conversational one) deploys only to Microsoft Teams, Slack, Email Bot, and the Support Portal. Freddy AI Agent Studio's newer MCP Gateway lets Freddy *pull* context from other tools — it doesn't let another chat platform *call into* Freddy.

So this service talks to the same knowledge base and ticketing data Freddy uses, through Freshservice's documented REST API, and is architected so `HelpdeskService.handle()` is the only place you'd need to change if Freshworks ships a supported custom-channel API later. If your org later gets access to a specific Freddy conversation endpoint, drop the call into that method and everything else — auth, sessions, rate limiting, Cliq formatting — stays the same.

## Architecture

```
Zoho Cliq user
   |  direct message
   v
Cliq Bot "Message Handler" (Deluge, runs inside Cliq)   <- deluge/message_handler.dg
   |  invokeUrl POST, X-Webhook-Secret header
   v
FastAPI  POST /webhooks/cliq                              <- app/main.py
   |
   |- verify shared secret / rate limit
   |- parse payload                                        app/cliq.py
   |- HelpdeskService.handle()                              app/service.py
   |     |- multi-turn session state (memory or Redis)      app/session_store.py
   |     `- FreshserviceClient (retries, routing)            app/freshservice.py
   v
JSON {"text": ...} or {"text","card","buttons"}
   |
   v
Deluge Message Handler returns it as the bot's reply
```

**This is the part most starter kits get wrong**: Cliq's *Incoming Webhook Handler* is for the opposite direction (external services notifying a channel — e.g. a monitoring alert). It cannot be pointed at your API to answer a live message. The thing that actually lets a user's Cliq message reach this API is the bot's **Message Handler**, which is Deluge code that runs inside Cliq and calls out via `invokeUrl`. See `deluge/message_handler.dg` — you paste that into the bot's handler editor, it isn't something this FastAPI service can install for you.

## 1. Configure the API

```bash
cp .env.example .env
```

Required:
- `WEBHOOK_SHARED_SECRET` — random value, 16+ characters. This is the *only* credential protecting this endpoint, since it's what the Deluge script sends as `X-Webhook-Secret`. Rotate it if it ever leaks into a log or a shared script.
- `FRESHSERVICE_DOMAIN`, `FRESHSERVICE_API_KEY` — use a dedicated integration agent with least-privilege scopes, not a personal API key.

Optional but recommended for production:
- `TICKET_ROUTING_RULES` — JSON keyword -> `{group_id, category}` map, so common issues land with the right team automatically.
- `SESSION_BACKEND=redis` + `REDIS_URL` — only needed once you run more than one replica; without it, a user's multi-turn "create ticket" reply could land on a different process than the one that asked the question.
- `CLIQ_BOT_UNIQUE_NAME` / `CLIQ_WEBHOOK_TOKEN` — only needed if you want to post an async follow-up message after the synchronous reply (see `app/cliq_client.py`).

## 2. Run locally

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready   # actually checks Freshservice creds work

curl -X POST http://localhost:8000/webhooks/cliq \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: replace-with-a-long-random-value" \
  -d '{"message":{"text":"reset Zoho Mail MFA"},"user":{"name":"Test User","email":"test@example.com"},"chat":{"id":"chat-1"}}'
```

## 3. Run with Docker

```bash
docker compose up -d --build
# only if SESSION_BACKEND=redis:
docker compose --profile redis up -d --build
```

The image runs as a non-root user and has a built-in `HEALTHCHECK`.

### Deploying the container

The Docker image **does not include** your `.env` file. If you deploy the built image to Azure App Service, Container Apps, Kubernetes, or run `docker run` directly, you must inject configuration as **environment variables** on the host.

Minimum required settings:

| Variable | Description |
|---|---|
| `WEBHOOK_SHARED_SECRET` | Same secret the Deluge Message Handler sends as `X-Webhook-Secret` (16+ chars) |
| `FRESHSERVICE_DOMAIN` | e.g. `yourcompany.freshservice.com` |
| `FRESHSERVICE_API_KEY` | Dedicated Freshservice integration API key |

Examples:

```bash
# docker run
docker run -p 8000:8000 \
  -e WEBHOOK_SHARED_SECRET='your-long-random-secret' \
  -e FRESHSERVICE_DOMAIN='yourcompany.freshservice.com' \
  -e FRESHSERVICE_API_KEY='your-api-key' \
  your-image:tag
```

```bash
# Docker Compose on a server: copy .env.example -> .env, fill values, then:
docker compose up -d --build
```

On Azure App Service / Container Apps, add the same three variables under **Application settings** / **Environment variables** in the portal (or your IaC template). Optional: set `ENV_FILE=/path/to/mounted/.env` if you mount a secrets file into the container instead of individual vars.

## 4. Expose the endpoint

Terminate TLS in front of this service — Azure API Management, Application Gateway, Nginx, Cloudflare Tunnel, or equivalent. Cliq's `invokeUrl` requires HTTPS.

```
POST https://your-host/webhooks/cliq
X-Webhook-Secret: <WEBHOOK_SHARED_SECRET>
```

## 5. Link the Mzito bot (Zoho Cliq)

This service connects to the **Mzito** bot (`Digital Division` team) on `cliq.zoho.com`.

| Direction | Endpoint |
|---|---|
| Cliq → API (Message Handler) | `POST https://<your-host>/webhooks/cliq` |
| API → Cliq (async replies, optional) | `https://cliq.zoho.com/api/v2/bots/mzito/message` |
| External → Cliq (incoming webhook, optional) | `https://cliq.zoho.com/api/v2/bots/mzito/incoming` |

### A. Wire the Message Handler (required)

This is what makes Mzito reply using Freshservice when users DM the bot.

1. In Zoho Cliq go to **Integrations → Bots → Mzito → Handlers → Message Handler**.
2. Paste the contents of `deluge/message_handler.dg`.
3. Edit the two lines at the top:
   - `WEBHOOK_URL` — your deployed API URL, e.g. `https://your-host.example.com/webhooks/cliq`
   - `WEBHOOK_SECRET` — must match `WEBHOOK_SHARED_SECRET` in your API deployment env
4. **Publish** the handler.
5. Direct-message **Mzito** and send `help` to test.

> **Tip:** Store `WEBHOOK_SECRET` in a Cliq **Connection** (Integrations → Connections) instead of hard-coding it in Deluge, then reference that connection in `invokeUrl`.

### B. Configure the API side

Set these in `.env` (local) or your container host env (production):

```env
WEBHOOK_SHARED_SECRET=<same secret as Deluge WEBHOOK_SECRET>
PUBLIC_BASE_URL=https://your-host.example.com
CLIQ_BOT_UNIQUE_NAME=mzito
CLIQ_DC=com
```

For optional async/proactive replies from the API back into Cliq:

1. In Cliq go to **Integrations → Webhook Tokens** and create a token scoped to the Mzito bot.
2. Set `CLIQ_WEBHOOK_TOKEN=<that token>` in the API env.

### C. Verify

```bash
curl -X POST https://your-host/webhooks/cliq \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: <WEBHOOK_SHARED_SECRET>" \
  -d '{"message":{"text":"help"},"user":{"name":"Test User","email":"test@example.com"},"chat":{"id":"chat-1"}}'
```

If the curl works but Mzito does not reply in Cliq, the Message Handler is not published or `WEBHOOK_URL` / `WEBHOOK_SECRET` in Deluge do not match the API.

Expected response body: `{"text": "..."}`, optionally with `"card"` and `"buttons"` for rich knowledge-base results.

## Commands

- `help`
- Any normal question, e.g. `How do I reset Zoho Mail MFA?`
- `create ticket: Unable to sign in to D&S GO` (or just `create ticket` — the bot will ask what's wrong)
- `status 12345` — only shows details if the requesting Cliq user's email matches the ticket's requester
- `cancel` — abandons a ticket you're in the middle of creating

## What's handled beyond the original starter

- **Retries with backoff** on Freshservice 429/5xx/network errors (`app/freshservice.py`), not on 4xx (retrying a bad request just wastes the org's API quota).
- **Multi-turn ticket creation** — if someone just says "create ticket", the bot asks what's wrong instead of rejecting the message; state lives in `app/session_store.py` (in-memory by default, Redis for multi-replica deployments) and expires automatically.
- **Ticket status authorisation** — `status <id>` now checks the ticket's requester email against the Cliq user before revealing anything, closing the "anyone can view anyone's ticket" gap the original starter flagged but didn't fix.
- **Keyword-based routing** (`TICKET_ROUTING_RULES`) so tickets can land in the right Freshservice group/category automatically.
- **Escalation nudge** — after two knowledge-base misses in the same conversation, the bot proactively suggests filing a ticket instead of repeating "no results found".
- **Rate limiting** per Cliq user (`app/rate_limit.py`), so one runaway loop or user can't burn the org's Freshservice API quota.
- **Structured JSON logs with secret redaction** (`app/logging_utils.py`) and a request-id on every log line and response header, for tracing one Cliq conversation through the logs.
- **`/ready` probe** that actually calls Freshservice, so a bad API key fails a deployment health check instead of failing silently on the first real user.
- **Rich Cliq responses** — knowledge-base results render as a card with clickable article buttons instead of plain-text links, when the article has a URL.
- **Non-root, health-checked Docker image**; GitHub Actions CI that runs the test suite and builds the image on every push.
- **The actual Cliq-side wiring** (`deluge/message_handler.dg`) — the original starter assumed Cliq would call the webhook automatically; it doesn't, this script is what makes that connection.

## Production hardening checklist

- [x] Retries with backoff on transient Freshservice errors
- [x] Per-user rate limiting
- [x] Requester-authorised ticket status lookups
- [x] Structured logs with secret/PII redaction
- [ ] Store `FRESHSERVICE_API_KEY` and `WEBHOOK_SHARED_SECRET` in a secret manager (Azure Key Vault, etc.), not `.env`, in production
- [ ] Put the endpoint behind a gateway with IP allow-listing where feasible
- [ ] If you add an LLM step later (e.g. to rephrase KB snippets into a more conversational answer), define data-handling, grounding and prompt-injection controls first — `HelpdeskService.handle()` in `app/service.py` is the one integration point to change

## Run tests

```bash
pytest -q
```

21 tests cover payload parsing, ticket creation (single-turn and multi-turn), status authorisation, KB escalation, Freshservice retry/no-retry behaviour, keyword routing, session expiry, and rate limiting.
