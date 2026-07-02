# Action Guardrail

A policy engine for AI agents that intercepts tool calls **before** execution and
evaluates them against declarative rules. Built with FastAPI for local development,
designed to be deployable to production with a pluggable storage backend.

## Quick Start

```bash
pip install -r requirements.txt
pytest -v        # 46 tests — all must pass
uvicorn app.main:app --reload   # start the API server
```

## Rule Schema

Each rule declares:

| Field        | Description                                    |
| ------------ | ---------------------------------------------- |
| `id`         | Unique rule identifier                         |
| `description`| Human-readable description                     |
| `priority`   | Lower number = evaluated first                 |
| `action`     | `block`, `require_hitl`, or `log_and_allow`    |
| `match.tool` | Tool name this rule applies to                 |
| `match.conditions` | List of conditions (all must match — AND logic) |

### Example

```yaml
rules:
  - id: "block-bulk-delete"
    description: "Block any database delete exceeding 100 records"
    priority: 10
    action: block
    match:
      tool: "delete_records"
      conditions:
        - field: "record_count"
          operator: "gt"
          value: 100
```

### Supported Operators

`eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `contains`, `regex`

## API Endpoints

### `POST /evaluate` — Core policy evaluation

```bash
# Block: delete >100 records
curl -s -X POST http://localhost:8000/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "tool_call": {"tool": "delete_records", "parameters": {"record_count": 500}},
    "dry_run": false
  }' | python -m json.tool

# Require HITL: email to external domain
curl -s -X POST http://localhost:8000/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "tool_call": {"tool": "send_email", "parameters": {"recipient_domain": "gmail.com"}},
    "dry_run": false
  }' | python -m json.tool

# Log & allow: confidential file read
curl -s -X POST http://localhost:8000/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "tool_call": {"tool": "read_file", "parameters": {"path": "/data/confidential/report.pdf"}},
    "dry_run": false
  }' | python -m json.tool

# Default allow: no rule matches
curl -s -X POST http://localhost:8000/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "tool_call": {"tool": "unknown_tool", "parameters": {}},
    "dry_run": false
  }' | python -m json.tool

# Dry-run mode: simulates without enforcement
curl -s -X POST http://localhost:8000/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "tool_call": {"tool": "delete_records", "parameters": {"record_count": 500}},
    "dry_run": true
  }' | python -m json.tool
```

### `GET /health` — Health check

```bash
curl -s http://localhost:8000/health | python -m json.tool
```

### `GET /hitl/pending` — List pending HITL requests

```bash
curl -s http://localhost:8000/hitl/pending | python -m json.tool
```

### `POST /hitl/{id}/approve` — Approve a HITL request

```bash
curl -s -X POST http://localhost:8000/hitl/<REQUEST_ID>/approve \
  -H "Content-Type: application/json" \
  -d '{"resolved_by": "admin-1"}' | python -m json.tool
```

### `POST /hitl/{id}/reject` — Reject a HITL request

```bash
curl -s -X POST http://localhost:8000/hitl/<REQUEST_ID>/reject \
  -H "Content-Type: application/json" \
  -d '{"resolved_by": "admin-1"}' | python -m json.tool
```

### `GET /audit-log` — Query audit log

```bash
curl -s "http://localhost:8000/audit-log?limit=10&outcome=block" | python -m json.tool
```

## Architecture

```
evaluate_action(tool_call, rules) → Decision
```

Rules are evaluated in priority order. The **first matching rule** wins. If no
rule matches, the default decision is `allow`.

### Storage Abstraction

`StorageBackend` is an abstract base class with three implementations:

| Backend | Class | Env value | Use case |
|---------|-------|-----------|----------|
| In-memory | `InMemoryStorage` | `memory` | Local dev / tests |
| DynamoDB | `DynamoDBStorage` | `dynamodb` | AWS Lambda |
| MongoDB | `MongoStorage` | `mongo` | Hugging Face Spaces |

Swap by changing the `STORAGE_BACKEND` env var (default: `mongo`).

## Deployment (AWS — always-free tier)

Deploy the guardrail API to AWS Lambda + API Gateway + DynamoDB, all within
the [AWS Free Tier](https://aws.amazon.com/free/) (no paid services).

### Architecture

```
Agent / Harness  ──HTTPS──>  API Gateway HTTP API  ──>  Lambda  ──>  DynamoDB
                                  │                                  │
                                  └── SSM Parameter Store             │
                                     (API key)                  Audit Log +
                                                                 HITL Queue
```

### Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| [AWS CLI](https://aws.amazon.com/cli/) | 2.x | `pip install awscli` or installer |
| [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) | 1.x+ | `pip install aws-sam-cli` |
| Docker | Desktop | Required by `sam build` for dependencies |

Configure the AWS CLI with credentials that have sufficient permissions
(AdministratorAccess or a scoped policy covering CloudFormation, Lambda,
API Gateway, DynamoDB, SSM, IAM, and CloudWatch Logs).

### Deploy

```powershell
# First deployment (guided prompts)
.\deploy.ps1

# Subsequent deployments (uses saved samconfig.toml)
.\deploy.ps1 -DeployOnly
```

**Guided deploy prompts — what to expect:**

| Prompt | Recommended value |
|--------|-------------------|
| Stack Name | `action-guardrail` |
| AWS Region | `us-east-1` |
| Parameter ApiKeyParamValue | A random string (e.g. `pwgen -s 32 1`) |
| Confirm changes before deploy | `N` |
| Allow SAM CLI IAM role creation | `Y` |
| Disable rollback | `N` |
| Save arguments to samconfig.toml | `Y` |

After deploy completes, the script prints the API Gateway URL.

### Smoke test

```powershell
.\deploy\smoke_test.ps1 -Endpoint "https://abc123.execute-api.us-east-1.amazonaws.com" -ApiKey "your-api-key"
```

Expected output:
```
=== Smoke Test: https://abc123.execute-api.us-east-1.amazonaws.com ===
  [PASS] GET /health
  [PASS] POST /evaluate -> block (delete >100 records)
  [PASS] POST /evaluate -> require_hitl (external email)
  [PASS] POST /evaluate -> log_and_allow (confidential file)
  [PASS] POST /evaluate -> allow (unknown tool)

+-------------------+
| ALL 5 PASSED  ✓ |
+-------------------+
```

### Run the harness against the cloud endpoint

```powershell
$env:GUARDRAIL_API_URL = "https://abc123.execute-api.us-east-1.amazonaws.com"
python harness\run_all.py      # requires GROQ_API_KEY in .env
```

The harness runs all 4 LLM-driven scenarios through the deployed guardrail
just as it does locally.

### Run the harness against the HF Space endpoint

```powershell
$env:GUARDRAIL_API_URL = "https://username-action-guardrail.hf.space"
python harness\run_all.py      # requires GROQ_API_KEY in .env
```

---

## Deployment (Hugging Face Spaces — always-free tier)

Deploy the guardrail API to [Hugging Face Spaces](https://huggingface.co/spaces)
using Docker + MongoDB Atlas M0 free cluster. No credit card required for
either service.

### Architecture

```
                                   ┌──────────────────┐
                                   │  Docker Space     │
Agent / Harness  ──HTTPS──>       │  (port 7860)      │
                                   │  │                │
                                   │  ├─ FastAPI app   │
                                   │  ├─ MongoStorage  │
                                   │  └─ policy files  │
                                   └────────┬─────────┘
                                            │
                                    ┌───────┴────────┐
                                    │  MongoDB Atlas  │
                                    │  M0 (free tier) │
                                    │  Audit Log +    │
                                    │  HITL Queue     │
                                    └────────────────┘
```

### Prerequisites

1. **MongoDB Atlas M0 Cluster** (no credit card required)
   - Go to https://www.mongodb.com/cloud/atlas/register — sign up with an email.
   - Create an **M0 free cluster** (choose any cloud provider, e.g. AWS / us-east-1).
   - Under **Security → Database Access**, create a database user + password.
   - Under **Security → Network Access**, add `0.0.0.0/0` (allow all).
   - Click **Connect** → **Drivers** → copy the **connection string** (e.g.
     `mongodb+srv://<user>:<password>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority`).
   - Keep this string private.

2. **Hugging Face account**
   - Create a free account at https://huggingface.co/join.
   - Create a new Space: https://huggingface.co/new-space
   - Choose **Space name** (e.g. `action-guardrail`), **Docker** as SDK.
   - Select **Docker → Python 3.12** base image (or leave default).

3. **Git** (to push the repo to the Space)

### Deploy

```bash
# 1. Clone the Space repository
git clone https://huggingface.co/spaces/<your-org>/<space-name>
cd <space-name>

# 2. Copy the guardrail source into the Space repo
#    (or set the Space's Git remote as a second remote on this repo)
#
#    Option A: copy files
cp -r ../path/to/guardrail/Dockerfile .
cp -r ../path/to/guardrail/.dockerignore .
cp -r ../path/to/guardrail/app/ ./app/
cp -r ../path/to/guardrail/policies/ ./policies/
cp ../path/to/guardrail/requirements.txt .
cp ../path/to/guardrail/.env.example .env

# 3. Set secrets in Hugging Face Space Settings → Repository secrets
#    Required:
#       MONGO_URI  = mongodb+srv://<user>:<password>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
#       API_KEY    = a random string (e.g. openssl rand -hex 32)
#
#    Recommended (if running harness):
#       GROQ_API_KEY = gsk_...  from https://console.groq.com

# 4. Commit and push
git add -A
git commit -m "Deploy Action Guardrail"
git push
```

HF Spaces will automatically build the Docker image and start the service.
The first build takes 2-5 minutes. After deployment, your Space URL will be:
`https://<your-org>-<space-name>.hf.space`

### Smoke test

```powershell
.\deploy\smoke_test_hf.ps1 -Endpoint "https://<your-org>-<space-name>.hf.space" -ApiKey "your-api-key"
```

Expected output:
```
=== Smoke Test (HF Spaces): https://<your-org>-<space-name>.hf.space ===
  [PASS] GET /health
  [PASS] POST /evaluate -> block (delete >100 records)
  [PASS] POST /evaluate -> require_hitl (external email)
  [PASS] POST /evaluate -> log_and_allow (confidential file)
  [PASS] POST /evaluate -> allow (unknown tool)

+-------------------+
| ALL 5 PASSED  ✓ |
+-------------------+
```

### Run the harness against the HF Space endpoint

```powershell
$env:GUARDRAIL_API_URL = "https://<your-org>-<space-name>.hf.space"
python harness\run_all.py      # requires GROQ_API_KEY in .env
```

### Free-tier cost breakdown

#### AWS (Lambda + DynamoDB deployment)

| Service | Free tier limit | Monthly usage at 10k evaluations | Charges |
|---------|----------------|----------------------------------|---------|
| **Lambda** | 1M requests + 400,000 GB-seconds | ~10k requests × 0.5 GB × 0.1s = 500 GB-seconds | **$0** |
| **API Gateway HTTP API** | 1M calls (12 months) | 10k calls | **$0** |
| **DynamoDB** | 25 GB storage + 25 RCU/WCU | ~1 MB + <1 RCU/WCU | **$0** |
| **SSM Parameter Store** | 10,000 parameters (standard) | 1 parameter × ~4 reads/cold-start | **$0** |
| **CloudWatch Logs** | 5 GB ingestion (first month) | ~1 MB | **$0** |
| **Total** | | | **$0/month** |

#### MongoDB Atlas / HF Spaces

| Service | Free tier limit | Monthly usage at 10k evaluations | Charges |
|---------|----------------|----------------------------------|---------|
| **MongoDB Atlas M0** | 512 MB storage, shared vCPU | ~1 MB | **$0** |
| **Hugging Face Spaces** | Up to 3 spaces (free CPU tier) | 1 space with Docker, ~1 GB RAM | **$0** |
| **Total** | | | **$0/month** |

If traffic exceeds free-tier limits:
- **Lambda**: ~$0.20 per million requests + ~$0.0000166667 per GB-second beyond quota
- **API Gateway**: ~$1.00 per million requests after 12-month free tier
- **DynamoDB**: PAY_PER_REQUEST = ~$1.25 per million writes, ~$0.25 per million reads
- **CloudWatch Logs**: ~$0.50 per GB ingested
- **MongoDB Atlas**: M2 (~$9/month) or M5 (~$25/month) for higher tiers
- **HF Spaces**: Pro ($9/month) for persistent CPU, no cold starts

### Project Layout

```
guardrail/
├── app/
│   ├── main.py           # FastAPI app + routes
│   ├── lambda_handler.py # Lambda entrypoint (Mangum)
│   ├── models.py         # Pydantic models
│   ├── evaluator.py      # Core evaluation logic
│   ├── policy_loader.py  # YAML rule loader
│   ├── storage.py        # Storage interface + InMemory / DynamoDB
│   ├── audit.py          # Audit log logic
│   ├── hitl.py           # HITL queue logic
│   └── config.py         # Settings (env / SSM)
├── harness/
│   ├── agent.py          # LLM agent loop (Groq)
│   ├── tools.py          # Mock tool schemas + executors
│   ├── guardrail_client.py
│   ├── scenarios.py      # Scripted test scenarios
│   ├── run_all.py        # Bootstrap + run all scenarios
│   └── run_scenarios.py  # Standalone scenario runner
├── policies/
│   └── example_rules.yaml
├── deploy/
│   ├── smoke_test.ps1       # Post-deploy smoke test (AWS)
│   └── smoke_test_hf.ps1    # Post-deploy smoke test (HF Spaces)
├── tests/
│   ├── conftest.py
│   ├── test_evaluator.py
│   ├── test_policy_loader.py
│   ├── test_audit.py
│   ├── test_hitl.py
│   ├── test_main.py
│   └── test_harness.py
├── Dockerfile             # Container image for HF Spaces
├── .dockerignore
├── template.yaml          # SAM deployment template
├── deploy.ps1             # Deployment script (AWS)
├── .env.example           # Environment variable template
└── requirements.txt
```
