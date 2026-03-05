# CLAUDE.md — AI Assistant Guide for Anthropic-number-one

This file provides essential context for AI assistants (Claude, Copilot, etc.) working on this repository.

---

## Project Overview

**Anthropic-number-one** is an N8N-based automation project for creating Facebook (Meta) Ads campaigns programmatically. It packages an N8N workflow and a Docker Compose setup so any developer can spin up a local automation server and trigger full campaign creation (campaign → ad set → ad creative → ad) via the Facebook Graph API.

**Technology stack:**

| Component | Technology |
|-----------|-----------|
| Automation platform | [N8N](https://n8n.io/) (self-hosted via Docker) |
| Containerisation | Docker Compose |
| External API | Facebook Graph API v19.0 |
| Credential management | `.env` file (never committed) |

---

## Repository Structure

```
Anthropic-number-one/
├── CLAUDE.md                          # This file
├── .env.example                       # Environment variable template (commit this, not .env)
├── .gitignore                         # Excludes .env, n8n_data/, node_modules/
├── docker-compose.yml                 # N8N service definition
└── workflows/
    └── facebook_ads_automation.json   # N8N workflow definition (the core logic)
```

### Key files explained

#### `docker-compose.yml`
Defines a single `n8n` service:
- Image: `n8nio/n8n:latest`
- Exposed port: `5678` (N8N web UI and REST API)
- Volumes:
  - `n8n_data:/home/node/.n8n` — persistent N8N state (credentials, execution logs, etc.)
  - `./workflows:/home/node/.n8n/workflows-import` — mounts local workflow JSON files for import
- Environment variables sourced from `.env`
- Restart policy: `unless-stopped`

#### `workflows/facebook_ads_automation.json`
Complete N8N workflow with 10 sequential nodes:

| # | Node | Action |
|---|------|--------|
| 1 | Manual Trigger | Starts the workflow on demand |
| 2 | Set Config | Loads all campaign parameters from env vars |
| 3 | Create Campaign | POST `/act_<ID>/campaigns` — objective: OUTCOME_TRAFFIC, status: PAUSED |
| 4 | Merge Campaign ID | Extracts `campaign_id` into the data stream |
| 5 | Create Ad Set | POST `/act_<ID>/adsets` — daily budget, targeting, optimization for REACH |
| 6 | Merge Ad Set ID | Extracts `adset_id` |
| 7 | Create Ad Creative | POST `/act_<ID>/adcreatives` — link data, image, CTA |
| 8 | Merge Creative ID | Extracts `creative_id` |
| 9 | Create Ad | POST `/act_<ID>/ads` — links creative to ad set, status: PAUSED |
| 10 | Output Summary | Returns all created resource IDs as JSON |

**All ads are created in `PAUSED` status** — manual activation in Meta Business Manager is required before they go live.

---

## Environment Variables

Copy `.env.example` to `.env` and populate before running Docker Compose.

```bash
cp .env.example .env
```

| Variable | Description | Example |
|----------|-------------|---------|
| `N8N_USER` | N8N basic-auth username | `admin` |
| `N8N_PASSWORD` | N8N basic-auth password | `changeme` |
| `FB_ACCESS_TOKEN` | Meta/Facebook long-lived access token | `EAAxxxxx...` |
| `FB_AD_ACCOUNT_ID` | Meta ad account ID (must include `act_` prefix) | `act_123456789` |
| `N8N_HOST` | Host for N8N webhooks/callbacks | `localhost` |

**Never commit `.env`.** It is listed in `.gitignore`.

---

## Development Workflows

### 1. Start the automation server

```bash
docker compose up -d
```

N8N will be available at `http://localhost:5678`. Login with `N8N_USER` / `N8N_PASSWORD`.

### 2. Import the workflow

On first run, N8N will automatically pick up JSON files from the `workflows-import` directory.
To re-import manually: N8N UI → Workflows → Import from File → select `workflows/facebook_ads_automation.json`.

### 3. Run the workflow

In the N8N UI, open "Facebook Ads Automation" and click **Execute Workflow**. The workflow runs synchronously and returns a summary JSON with all created resource IDs.

### 4. Stop the server

```bash
docker compose down
```

To also delete persistent data (N8N credentials, execution history):
```bash
docker compose down -v
```

### 5. Update workflow logic

Edit `workflows/facebook_ads_automation.json` directly, then re-import in the N8N UI, **or** edit nodes visually in N8N and export the updated workflow back to `workflows/` to keep the file in sync with the running instance.

---

## Default Campaign Configuration

The **Set Config** node in the workflow uses these defaults (edit node to change):

| Parameter | Default Value |
|-----------|---------------|
| Campaign name | `Automated Campaign - N8N` |
| Campaign objective | `OUTCOME_TRAFFIC` |
| Daily budget | `$5.00 USD` (stored as 500 cents) |
| Bid amount | `$2.00 USD` (stored as 200 cents) |
| Ad title | `Check out our latest offer!` |
| Landing page URL | *(set in workflow — update before going live)* |
| Image URL | *(set in workflow — update before going live)* |
| Targeting countries | `US` |
| Targeting ages | 18–65 |
| Ad status | `PAUSED` |

---

## Conventions & Best Practices

### Workflow JSON
- Keep `workflows/facebook_ads_automation.json` as the single source of truth.
- After editing nodes in the N8N UI, always export and overwrite the JSON file so the repo stays current.
- Node names should be descriptive (e.g., "Create Campaign", "Merge Ad Set ID") — the current naming convention matches this.
- Do not hardcode credentials inside workflow nodes — always use N8N environment variable expressions (`={{ $env.FB_ACCESS_TOKEN }}`).

### Environment & Secrets
- Add new secrets to `.env.example` with placeholder values and a short comment.
- Document every new variable in the table in this file.

### Docker
- Pin the N8N image version in `docker-compose.yml` when moving to production (e.g., `n8nio/n8n:1.x.x`) to avoid unexpected breaking changes from `:latest`.
- The `n8n_data` volume holds credentials encrypted by N8N. Back it up before upgrading N8N.

### Git
- Development branch pattern: `claude/<session-id>`
- The default integration branch is `master`
- Do not commit `.env`, `n8n_data/`, or `node_modules/` — all are in `.gitignore`

---

## Facebook Graph API Notes

- **API version in use:** `v19.0` — update the version string in all HTTP Request nodes if upgrading.
- The workflow targets the `/act_<ID>/` endpoints for campaigns, ad sets, creatives, and ads.
- A valid **long-lived User Access Token** or **System User Token** with `ads_management` permission is required.
- All newly created objects default to `PAUSED` — this is intentional to prevent accidental spend.
- Budget values are in **cents** (integer): `$5.00` = `500`.

---

## Common Issues & Troubleshooting

| Issue | Likely cause | Fix |
|-------|-------------|-----|
| N8N UI not loading | Container not running | `docker compose up -d` and check `docker compose logs` |
| `401 Unauthorized` from Facebook API | Expired or wrong token | Refresh `FB_ACCESS_TOKEN` in `.env` and restart the container |
| `Invalid ad account ID` error | Missing `act_` prefix | Ensure `FB_AD_ACCOUNT_ID=act_XXXXXXXXXX` |
| Workflow not visible after import | Import directory not mounted | Verify the `./workflows` volume mount in `docker-compose.yml` |
| N8N loses credentials after restart | Volume not persisted | Confirm the `n8n_data` named volume exists (`docker volume ls`) |

---

## Things That Don't Exist Yet (Potential Contributions)

- Webhook trigger node (replace manual trigger for event-driven execution)
- Error-handling branch in the workflow (currently linear, no failure path)
- Automated tests for workflow logic
- CI/CD pipeline (GitHub Actions) to validate JSON schema of workflow files
- README.md for human-facing documentation
- Multi-environment support (staging vs. production ad accounts)
