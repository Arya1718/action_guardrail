# Deployment — Hugging Face Spaces

## Overview

The primary deployment target is **Hugging Face Spaces** using the **Docker SDK**.
MongoDB Atlas M0 (free tier) provides persistent storage. Unlike the original
Lambda/SAM deployment, HF Spaces requires no AWS account and runs 24/7 on the
free tier.

## Prerequisites

- A [Hugging Face](https://huggingface.co) account
- A [MongoDB Atlas](https://cloud.mongodb.com) M0 cluster (free)
- Docker installed locally (for pushing)

## MongoDB Atlas Setup

1. Create an M0 (free) cluster at https://cloud.mongodb.com
2. Under **Network Access**, add IP `0.0.0.0/0` (allow all) — necessary because
   HF Spaces uses ephemeral IPs.
3. Under **Database Access**, create a user with read/write on the `guardrail`
   database.
4. Click **Connect** → **Drivers** → copy the connection string
   (`mongodb+srv://...`).

## HF Spaces Setup

1. Go to https://huggingface.co/new-space
2. Space name: e.g. `action-guardrail`
3. License: MIT
4. SDK: **Docker**
5. Hardware: CPU (free)
6. Click **Create Space**

## Deploy

### Option A: Git Push (Recommended)

```bash
# Clone the HF Space repo
git clone https://huggingface.co/spaces/your-username/action-guardrail
cd action-guardrail

# Add the guardrail project files
cp -r /path/to/guardrail/* .

# Commit and push
git add -A
git commit -m "Initial deploy"
git push
```

HF Spaces auto-detects the `Dockerfile` and builds/deploys.

### Option B: Docker Build + Push

```bash
# Build the image locally
docker build -t guardrail .

# Tag for HF Spaces registry
docker tag guardrail registry.hf.space/your-username/action-guardrail:latest

# Push
docker push registry.hf.space/your-username/action-guardrail:latest
```

## Configure Secrets (HF Spaces)

In the HF Space settings page, set these **Secrets** (environment variables):

| Secret | Value |
|---|---|---|
| `API_KEY` | Your chosen API key (e.g. `pwgen -s 32 1`) |
| `STORAGE_BACKEND` | `mongo` |
| `MONGO_URI` | Your Atlas connection string |
| `MONGO_DB_NAME` | `guardrail` |
| `SLACK_WEBHOOK_URL` | (optional) Slack webhook for block/require_hitl alerts |
| `ORG_API_KEYS` | (optional) JSON mapping of org_id → API key for multi-tenancy |

Do **not** set `GROQ_API_KEY` here — that is only needed for the local harness,
not for the API itself.

## Verify

```bash
# Root
curl -s https://your-username-action-guardrail.hf.space/

# Health
curl -s https://your-username-action-guardrail.hf.space/health

# Evaluate (with auth)
curl -s -X POST https://your-username-action-guardrail.hf.space/evaluate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your-api-key>" \
  -d '{"tool_call": {"tool": "delete_records", "parameters": {"record_count": 500}}}'

# Swagger UI
open https://your-username-action-guardrail.hf.space/docs
```

## Run the Harness Against Live

```powershell
$env:GUARDRAIL_API_URL = "https://your-username-action-guardrail.hf.space"
$env:GROQ_API_KEY = "gsk_..."
python harness\run_all.py
```

## Run the Harness Against Live

```bash
export GUARDRAIL_API_URL=https://your-username-action-guardrail.hf.space
export GUARDRAIL_API_KEY=your-api-key
export GROQ_API_KEY=gsk_...
python harness/run_all.py --auto-approve
```

## Alternate Deployment: AWS Lambda (always-free tier)

A second deployment targeting AWS Lambda + API Gateway HTTP API. Uses the same
MongoDB Atlas cluster but a **separate database** (`guardrail_aws`).

### Deploy

```powershell
# Prerequisites: AWS CLI, SAM CLI, Docker
aws sts get-caller-identity

# First deployment (guided prompts)
.\deploy-aws.ps1

# Subsequent deployments
.\deploy-aws.ps1 -DeployOnly
```

### Smoke test

```powershell
.\deploy\smoke_test_aws.ps1 -Endpoint "https://abc123.execute-api.us-east-1.amazonaws.com" -ApiKey "your-api-key"
```

### Run scenarios against AWS

```bash
export GUARDRAIL_API_URL=https://abc123.execute-api.us-east-1.amazonaws.com
export GUARDRAIL_API_KEY=your-api-key
python harness/run_all.py --auto-approve
```

See the main README.md for the API Gateway URL and API key.

## CloudShell (AWS browser terminal)

Open https://console.aws.amazon.com/cloudshell/ and run:

```bash
git clone -b feature/infrastructure-upgrade https://github.com/Arya1718/action_guardrail.git
cd action_guardrail
pip install -e .
export GUARDRAIL_API_URL=https://q6mucicr0e.execute-api.us-east-1.amazonaws.com
export GUARDRAIL_API_KEY=AryaGuardrail1804
python scripts/review_pending.py
```

## Teardown

- **HF Space**: Settings → Delete Space.
- **AWS**: `sam delete --stack-name action-guardrail-aws`
- **MongoDB Atlas**: Cluster → Settings → Delete Cluster.
