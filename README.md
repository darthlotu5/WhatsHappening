# Dundaa / WhatsHappening

Discover events across Kenya, hosted on Azure with Clerk authentication.

**Demo:** https://ca-web-whatshap-dev-04ce.yellowflower-e7938034.southafricanorth.azurecontainerapps.io

## Runtime

- Next.js / React frontend with Clerk sign-in and signup; public event browsing.
- Flask events and discovery APIs, with Gunicorn in deployed containers.
- Scraper -> Azure Blob Storage -> Azure Storage Queue -> processor -> Cosmos DB.
- A scheduled task requests fresh scraping; queued work is retried and acknowledged
  only after success. After five failed deliveries, messages go to a `-poison`
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

The scraper relies on external TicketSasa markup and availability. Upstream
failures are logged and retried, not replaced with fabricated events.
The historical RabbitMQ credential must be revoked/rotated at the provider;
removing it from current source does not erase Git history.

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
