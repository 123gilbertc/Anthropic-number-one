# CLAUDE.md — AI Assistant Guide

This file provides context for AI assistants working in this repository.

## Project Overview

**Anthropic-number-one** is an n8n-based workflow automation project for Facebook Ads campaign management. It uses Docker Compose to run an n8n instance and ships a pre-built workflow that creates a complete Facebook Ads funnel (Campaign → Ad Set → Ad Creative → Ad) via the Facebook Graph API.

## Repository Structure

```
Anthropic-number-one/
├── CLAUDE.md                          # This file
├── .env.example                       # Environment variable template (copy to .env)
├── .gitignore                         # Excludes .env, n8n_data/, node_modules/
├── docker-compose.yml                 # Spins up n8n on port 5678
└── workflows/
    └── facebook_ads_automation.json   # n8n workflow definition (10 nodes)
```

## Technology Stack

| Layer | Technology |
|---|---|
| Workflow engine | [n8n](https://n8n.io) (n8nio/n8n:latest Docker image) |
| Container runtime | Docker & Docker Compose v3.8 |
| External API | Facebook Graph API v19.0 |
| Runtime | Node.js (bundled inside n8n image) |

There is no package.json, no custom application code, and no test suite. All business logic lives in the n8n workflow JSON.

## Environment Variables

Copy `.env.example` to `.env` and fill in the two required secrets before starting the stack:

```
# n8n admin login
N8N_USER=admin
N8N_PASSWORD=changeme          # Change before any non-local deployment

# Facebook / Meta credentials
FB_ACCESS_TOKEN=               # Long-lived Meta user or system-user token
FB_AD_ACCOUNT_ID=act_XXXXXXXX  # Must include the "act_" prefix

# n8n host (change to your public hostname when deploying remotely)
N8N_HOST=localhost
```

**Never commit `.env` to git.** It is already listed in `.gitignore`.

## Development Workflow

### Starting the stack

```bash
cp .env.example .env       # first time only
# fill in FB_ACCESS_TOKEN and FB_AD_ACCOUNT_ID
docker compose up -d
```

n8n is available at **http://localhost:5678** (login with `N8N_USER` / `N8N_PASSWORD`).

### Stopping the stack

```bash
docker compose down
```

Data is persisted in the `n8n_data` Docker volume; stopping the container does not lose workflow state.

### Viewing logs

```bash
docker compose logs -f n8n
```

### Applying workflow changes

The `./workflows` directory is mounted into the container at `/home/node/.n8n/workflows-import`. n8n will auto-import JSON files from this path on start-up. After editing `facebook_ads_automation.json`:

1. `docker compose restart n8n`
2. Open the n8n UI and verify the workflow loaded correctly.

Alternatively, edit workflows directly in the n8n UI and export them back to the `workflows/` directory to keep the file in sync.

## Workflow: facebook_ads_automation.json

**File:** `workflows/facebook_ads_automation.json`

The workflow is a linear 10-node pipeline triggered manually:

```
Manual Trigger
    └─► Set Config          (inject env vars + ad copy defaults)
        └─► Create Campaign (POST /campaigns → Facebook Graph API)
            └─► Merge Campaign ID  (carry campaign_id forward)
                └─► Create Ad Set  (POST /adsets)
                    └─► Merge Ad Set ID
                        └─► Create Ad Creative  (POST /adcreatives)
                            └─► Merge Creative ID
                                └─► Create Ad   (POST /ads)
                                    └─► Output Summary
```

### Node responsibilities

| Node | Type | Purpose |
|---|---|---|
| Manual Trigger | manualTrigger | Starts execution from the n8n UI |
| Set Config | set (v3.4) | Reads `FB_ACCESS_TOKEN` and `FB_AD_ACCOUNT_ID` from env; sets defaults for campaign name, budget, ad copy, targeting |
| Create Campaign | httpRequest (v4.2) | Creates campaign with `OUTCOME_TRAFFIC` objective in `PAUSED` state |
| Merge Campaign ID | set | Passes `campaign_id` alongside credentials to next node |
| Create Ad Set | httpRequest | Creates ad set with daily budget, geo targeting, age range; `PAUSED` state |
| Merge Ad Set ID | set | Carries `adset_id` and `campaign_id` forward |
| Create Ad Creative | httpRequest | Creates `link_data` creative with title, body, image, CTA; requires `YOUR_PAGE_ID` to be set |
| Merge Creative ID | set | Carries `creative_id` forward |
| Create Ad | httpRequest | Creates the final ad; left in `PAUSED` state |
| Output Summary | set | Returns `ad_id`, `campaign_id`, `adset_id`, `creative_id`, and a status message |

### Configurable defaults (Set Config node)

| Field | Default value | Notes |
|---|---|---|
| `campaign_name` | `Automated Campaign - N8N` | |
| `campaign_objective` | `OUTCOME_TRAFFIC` | See Meta docs for other objectives |
| `daily_budget` | `500` | In cents (USD 5.00) |
| `currency` | `USD` | |
| `ad_title` | `Check out our latest offer!` | |
| `ad_body` | `Click to learn more about our amazing products.` | |
| `ad_link` | `https://yourwebsite.com` | Must be a real URL for Meta to approve |
| `image_url` | `https://yourwebsite.com/ad-image.jpg` | Publicly accessible image |
| `targeting_country` | `US` | ISO 3166-1 alpha-2 |
| `age_min` | `18` | |
| `age_max` | `65` | |

### Known placeholder requiring action

The **Create Ad Creative** node has a hardcoded `"page_id": "YOUR_PAGE_ID"` that must be replaced with a real Facebook Page ID before the workflow will succeed.

## Key Conventions

- **All resources are created in `PAUSED` state.** Activate them manually in Meta Business Manager after reviewing.
- **Data propagation pattern:** Each "Merge" node is a `set` node that re-assembles the fields needed by the next API call, because n8n HTTP Request nodes only output the API response. Always use `$('NodeName').item.json.<field>` to reference upstream data.
- **Credentials are environment-injected.** Secrets are never hardcoded in the workflow JSON; they flow from Docker Compose env vars into `$env.FB_ACCESS_TOKEN` / `$env.FB_AD_ACCOUNT_ID`.
- **API version:** All Graph API calls target `v19.0`. Update the URL base when upgrading.

## Adding New Workflows

1. Create a new JSON file in `workflows/` following n8n's export format.
2. Use the same node ID pattern (`aaaa000X-0000-0000-0000-00000000000X`) only if IDs must be stable; otherwise let n8n generate UUIDs.
3. Restart the stack so n8n imports the new file.

## Modifying the Facebook Ads Workflow

- Edit `workflows/facebook_ads_automation.json` directly **or** edit in the n8n UI and export.
- After any structural change (new nodes, new connections), verify `"connections"` at the bottom of the JSON matches the node names exactly — n8n uses node names (not IDs) as connection keys.
- Keep all new Facebook API calls in `PAUSED` status unless there is an explicit requirement to activate them automatically.

## Security Notes

- Rotate `FB_ACCESS_TOKEN` regularly; tokens expire or can be revoked.
- Change `N8N_PASSWORD` from the default `changeme` before exposing port 5678 to any network.
- Do not commit `.env` or any file containing live credentials.
- The `n8n_data` Docker volume contains the n8n SQLite database (credentials, execution history). Back it up and restrict access accordingly.

## Branching & Git

- Default branch: `master`
- Feature branches use the prefix `claude/` (e.g., `claude/add-claude-documentation-5KjV1`)
- There are no CI checks or pre-commit hooks configured at this time.
