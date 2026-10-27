# Dundaa / WhatsHappening

Discover events across Kenya, hosted on Azure with Clerk authentication.

**Demo:** https://ca-web-whatshap-dev-04ce.yellowflower-e7938034.southafricanorth.azurecontainerapps.io

## Runtime

- Next.js / React frontend with Clerk sign-in and signup; public event browsing.
- Flask events and discovery APIs, with Gunicorn in deployed containers.
- Scraper -> Azure Blob Storage -> Azure Storage Queue -> processor -> Cosmos DB.
- A scheduled task fans out one independent scrape message per registered source;
  transient failures are retried. After five failed deliveries, messages go to a `-poison`
  queue and an error is logged.

RabbitMQ is no longer used by the deployed runtime. Firebase accounts and passwords
are not automatically transferred to Clerk: existing users need to create a Clerk
account or follow a separately planned user migration.

## Configuration

Frontend: copy `frontend/.env.example` to `frontend/.env.local` and set Clerk keys
privately, or initialize a claimable development application with the Clerk CLI.
Never commit `.env.local` or a Clerk secret key. Set `EVENTS_API_URL` to the events
API's private base URL. Browser requests use the same-origin `/api/events` proxy;
recommended events require a Clerk session. The current recommendation endpoint
returns the catalog, not a personalized ranking.

Python services require `COSMOS_ACCOUNT_URI`, `AZURE_STORAGE_BLOB_ENDPOINT`, and
`AZURE_STORAGE_QUEUE_ENDPOINT`. Set `DISCOVERY_API_URL` on the events API.
Azure deployments use managed identity with Cosmos and Storage data-plane roles.
For local development, `az login` supplies credentials, or set
`AZURE_STORAGE_CONNECTION_STRING` and `COSMOS_ACCOUNT_KEY` privately.
Provision Cosmos database `dundaa` and container `site_events`, partitioned by
`/site_name`, before starting services.

Queue names: `dundaaevents-discovered-events` for ingestion and
`x-delayed-dundaaevents-discovered-events` for scraping.

## Run locally

Use Python 3.13+ and Node.js 20.9+ (Node 24 is recommended).

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m gunicorn --bind 0.0.0.0:8000 EventsApi.app:app
.venv/bin/python -m gunicorn --bind 0.0.0.0:5000 EventDiscoveryApi.app:app
.venv/bin/python -m EventsProcessor.app
.venv/bin/python -m EventScrapper.app
# In another terminal:
cd frontend
npm ci
npm run dev
```

Run each long-lived service in a separate terminal. To schedule a scrape:
`.venv/bin/python -m EventDiscoveryApi.schedule`.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
cd frontend
npm run typecheck
npm run build
E2E_BASE_URL=https://your-deployed-host npm run test:e2e
```

Source adapters rely on external markup, public interfaces, and availability.
Upstream failures are logged and retried, not replaced with fabricated events.
The historical RabbitMQ credential must be revoked/rotated at the provider;
removing it from current source does not erase Git history.

## Multi-source discovery and information gain

`EventScrapper/sources.py` registers the 19 unique directory entries (the repeated
Urban Gang entry is configured once). Every scheduled run queues a separate
message per source. A source outage does not prevent other sources from running.
Only public event facts are extracted; organizer descriptions, customer data,
login-protected content, and private APIs are not copied.

The worker's **durable Blob-backed state** survives scale-to-zero and deployments.
Each source tracks stable event IDs and SHA-256 fingerprints of normalized event
facts. Only new or changed records are published to ingestion; identical scans
produce no event blobs, processing messages, or Cosmos upserts. Fingerprints
advance only after publication succeeds. Queue delivery remains at-least-once;
retry-after-failure can repeat a delivery, and Cosmos IDs make those writes
idempotent. State updates use Blob ETags to reject concurrent lost updates.

HTTP requests use conditional `ETag`/`Last-Modified` validation. Detail pages use
adaptive 6-24-hour cache windows, capped by provider cache directives; listing pages are revalidated on every run.
This intentionally bounds how quickly detail-only changes can be observed.
`no-store`/`no-cache` responses retain their HTTP cache semantics. Old HTTP cache
entries expire after 30 days. The report's `information_gain` is the fraction of
observed events whose normalized content changed, not an estimate of unseen data.

Adapters validate observed pagination totals/termination, follow published
sitemaps where appropriate, and check full SSR catalog coverage. They respect
robots rules (including longest-path precedence), crawl delays, HTTPS host
allowlists, and per-host throttling. Denied access, rate limits, DNS failures,
parser changes, and truncated coverage are explicit source outcomes, not empty
successful results. Request/byte safety limits report incomplete coverage rather
than silently treating a capped first page as the full catalog. Some providers
cannot expose a complete catalog to an unauthenticated, policy-respecting crawler;
registration does **not** mean every source is fully accessible.

Inspect current source outcomes and information-gain metrics:

```sh
curl -fsS https://ca-web-whatshap-dev-04ce.yellowflower-e7938034.southafricanorth.azurecontainerapps.io/api/events/sources
```

Audit live public pages locally without publishing to the production catalog:

```sh
.venv/bin/python -m EventScrapper.audit \
  --state-dir .copilot-azure/source-audit --workers 3
# Repeat against the same directory to measure changed records and cache reuse.
# Restrict a run with --source ticketsasa --source nyotecreation.
```

The audit returns nonzero if any source is blocked, unavailable, or incomplete,
while printing every source outcome. Explicitly unannounced event dates display
as such; dates with a missing year preserve the publisher's date label and are
marked unconfirmed instead of guessing a year. Raw source records retain their
provenance. The catalog merges verified canonical URLs or exact normalized
name/venue/start-time matches across publishers while retaining source links;
undated entries are not merged merely because their names match.

The private discovery API also accepts `POST /api/scrap` with
`{"source":"ticketsasa"}` to queue just one source, or `{}` for all sources.
Incomplete/failed sources remain visible at `/api/events/sources` and in worker
logs. Transient errors use the existing retry/poison-queue policy; explicitly
robots/access-blocked sources are recorded and acknowledged without repeatedly
hammering a denied endpoint.
Known partial catalogs are also acknowledged with an explicit incomplete report;
they are revisited on the next schedule, not immediately recrawled five times.
Missing optional venues/end times are retained as unknown, not invented.

### Verified public-source coverage

These are observed public-page counts from 27 October 2026, not permanent
inventory totals. “Partial” means useful records are ingested but full platform
coverage is **not** verified. Current production counts and specific failures are
available from `/api/events/sources`.

The deployed scan produced **1,229 deduplicated catalog entries from 14
data-producing providers**. All 19 sources were attempted. A repeat NyoteCreation
scan after deploying a new worker revision reused all five cached details and
published zero updates, proving incremental state survives deployment.

| Source | Observed acquisition and coverage |
| --- | --- |
| Ticketsasa | 28 full SSR records; checked against the reported upcoming total |
| Mookh | 135 parseable records from 142 sitemap candidates; partial, with stale/detail failures and a robots-disallowed listing API |
| HustleSasa | 154 production events from 155 sitemap candidates after normalizing unpadded published clocks; one stale URL, with full marketplace API coverage unverified |
| M-Tickets | 55 sitemap events; partial platform scope and potentially expiring signed images |
| Tikiti | 330 events across all 14 published listing pages and detail pages |
| TipsiTickets | 73 distinct events across both exhausted current/past feeds |
| PataTicket | 3 current events; 14 historical cards need a robots-disallowed resolver |
| Tickets Kenya | 140 distinct events across all 11 listing pages |
| NyoteCreation | 5 events across the public listing/details |
| Rada | Explicitly empty public EventsExplorer catalog; articles are not events |
| NOW Kenya | Domain DNS unavailable during the audit |
| Urban Gang Tour | 11 full server-registry records, verified against the sitemap, including explicit TBA events |
| KenyaBuzz | 58 parent events expanded to 129 dated occurrences; landing and numbered lists are unioned |
| Visit Nairobi | Live feed is robots-disallowed; the stale KenyaBuzz snapshot is not republished as fresh data |
| Eventbrite Kenya | 110 records across 6 pages verified locally, with changing totals; the deployed Azure fetch currently returns HTTP 405, so these are not represented as a successful production scrape |
| Rada Nightlife | Empty DJ-event subset only; editorial articles are not ingested as events |
| TIKET | 4 active public records; anonymous API count/pagination verified |
| OnePass Africa | 112 distinct Kenya cards across 5 pages; published dates lack years and remain explicitly unconfirmed |
| AllEvents Nairobi | 58 parsed detail events from 64 observed links; partial because further API pagination is unavailable |

Occurrences sharing an event page but having different start times remain
separate. Repeating the audit preserved unchanged fingerprints, including all
135 Mookh, 140 Tickets Kenya, 129 KenyaBuzz, 112 OnePass, and 73 Tipsi records;
only newly discovered/changed event facts enter the ingestion pipeline.

## Azure deployment

The demo runs in **South Africa North**, subscription
`85041044-a067-4f83-8afd-599930e74e7d` (Fokas Labs), resource group
`rg-whatshap-dev-04ce`. Bicep provisions five scale-to-zero Container Apps,
a six-hourly scheduler job, ACR Basic, Cosmos DB Serverless, Storage queues/blobs,
Key Vault, and capped Log Analytics. Only the frontend has public ingress.
The estimated low-traffic cost is **$7-$13/month**, not a spending limit;
traffic, builds, egress, taxes, and shared free-grant consumption affect billing.
Cold starts are expected. This single-replica demo is not highly available.

Use the Azure CLI and Node.js 24 from the repository root. Authenticate with
`az login` and ensure your account can deploy resources and assign roles.
`infra/main.parameters.json` contains the demo's nonsecret resource metadata.
Resource names in the modules are fixed to this deployment; change them together
before deploying a separate environment.

First provision the foundation, without placeholder application images:

```sh
export AZURE_SUBSCRIPTION_ID=85041044-a067-4f83-8afd-599930e74e7d
az deployment sub what-if --subscription "$AZURE_SUBSCRIPTION_ID" \
  --location southafricanorth --template-file infra/main.bicep \
  --parameters @infra/main.parameters.json
az deployment sub create --subscription "$AZURE_SUBSCRIPTION_ID" \
  --name whatshap-foundation-04ce --location southafricanorth \
  --template-file infra/main.bicep --parameters @infra/main.parameters.json
```

Keep the existing Clerk development keys in `frontend/.env.local`. Do not
reinitialize Clerk on redeployment. Seed the secret via stdin, never a build
argument or plaintext deployment parameter. Do not enable shell tracing:

```sh
set -o pipefail
node --env-file=frontend/.env.local \
  -e 'process.stdout.write(process.env.CLERK_SECRET_KEY)' |
  az keyvault secret set --subscription "$AZURE_SUBSCRIPTION_ID" \
    --vault-name kv-whatshap-dev-04ce --name clerk-secret-key \
    --file /dev/stdin --encoding utf-8 --query id -o tsv
CLERK_PUBLIC_KEY=$(node --env-file=frontend/.env.local \
  -e 'process.stdout.write(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY)')
RELEASE=$(date -u +%Y%m%d%H%M%S)
az acr build --subscription "$AZURE_SUBSCRIPTION_ID" -r crwhatshapdev04ce \
  -t "backend:$RELEASE" -f infra/Dockerfile.backend --no-logs .
az acr build --subscription "$AZURE_SUBSCRIPTION_ID" -r crwhatshapdev04ce \
  -t "frontend:$RELEASE" -f infra/Dockerfile.frontend \
  --build-arg "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=$CLERK_PUBLIC_KEY" --no-logs .
BACKEND_DIGEST=$(az acr repository show --subscription "$AZURE_SUBSCRIPTION_ID" \
  -n crwhatshapdev04ce --image "backend:$RELEASE" --query digest -o tsv)
FRONTEND_DIGEST=$(az acr repository show --subscription "$AZURE_SUBSCRIPTION_ID" \
  -n crwhatshapdev04ce --image "frontend:$RELEASE" --query digest -o tsv)
az deployment sub what-if --subscription "$AZURE_SUBSCRIPTION_ID" \
  --location southafricanorth --template-file infra/main.bicep \
  --parameters @infra/main.parameters.json deployApps=true \
  "backendImage=crwhatshapdev04ce.azurecr.io/backend@$BACKEND_DIGEST" \
  "frontendImage=crwhatshapdev04ce.azurecr.io/frontend@$FRONTEND_DIGEST" \
  "nextPublicClerkPublishableKey=$CLERK_PUBLIC_KEY"
az deployment sub create --subscription "$AZURE_SUBSCRIPTION_ID" \
  --name whatshap-apps-04ce --location southafricanorth \
  --template-file infra/main.bicep --parameters @infra/main.parameters.json \
  deployApps=true "backendImage=crwhatshapdev04ce.azurecr.io/backend@$BACKEND_DIGEST" \
  "frontendImage=crwhatshapdev04ce.azurecr.io/frontend@$FRONTEND_DIGEST" \
  "nextPublicClerkPublishableKey=$CLERK_PUBLIC_KEY"
az containerapp job start --subscription "$AZURE_SUBSCRIPTION_ID" \
  -g rg-whatshap-dev-04ce -n job-scrape-whatshap-dev-04ce
```

Allow time for managed-identity role propagation and queue-based scaling.
The Azure CLI cloud builds do not need a local Docker daemon. Root
`.dockerignore` excludes local keys, environment files, and browser traces.
After ingestion, run the browser suite above against the demo HTTPS URL. It
requires nonempty persisted data, verifies the Clerk signup/OTP/session/logout
flow, and deletes its temporary Clerk test user. Test addresses use the reserved
`+clerk_test` subaddress and do not receive real email.

The current Clerk instance is a claimable **development** instance; claim it
through the local Clerk CLI before configuring production keys/domain settings.
Development credentials and OTP shortcuts are not a production authentication
configuration.
