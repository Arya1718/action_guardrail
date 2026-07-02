---
title: Action Guardrail
emoji: 🛡️
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
---

# Action Guardrail

> **Live demo**: https://AntiSpiral18-action-guardrail.hf.space

A policy engine for AI agents that intercepts tool calls **before** execution and
evaluates them against declarative rules. Built with FastAPI for local development,
designed to be deployable to production with a pluggable storage backend.

## Live Public Deployment

**https://AntiSpiral18-action-guardrail.hf.space**

This is a real, publicly accessible API running on Hugging Face Spaces free
tier with MongoDB Atlas M0 storage. Anyone can hit it — that's the point.
Endpoint: `POST /evaluate` with `X-API-Key` and a `tool_call` payload.

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

`StorageBackend` is an abstract base class with in-memory (`InMemoryStorage`) and
future DynamoDB implementations. Swap by changing `STORAGE_BACKEND` env var.

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

### Free-tier cost breakdown

| Service | Free tier limit | Monthly usage at 10k evaluations | Charges |
|---------|----------------|----------------------------------|---------|
| **Lambda** | 1M requests + 400,000 GB-seconds | ~10k requests × 0.5 GB × 0.1s = 500 GB-seconds | **$0** |
| **API Gateway HTTP API** | 1M calls (12 months) | 10k calls | **$0** |
| **DynamoDB** | 25 GB storage + 25 RCU/WCU | ~1 MB + <1 RCU/WCU | **$0** |
| **SSM Parameter Store** | 10,000 parameters (standard) | 1 parameter × ~4 reads/cold-start | **$0** |
| **CloudWatch Logs** | 5 GB ingestion (first month) | ~1 MB | **$0** |
| **Total** | | | **$0/month** |

If traffic exceeds free-tier limits:
- **Lambda**: ~$0.20 per million requests + ~$0.0000166667 per GB-second beyond quota
- **API Gateway**: ~$1.00 per million requests after 12-month free tier
- **DynamoDB**: PAY_PER_REQUEST = ~$1.25 per million writes, ~$0.25 per million reads
- **CloudWatch Logs**: ~$0.50 per GB ingested

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
│   ├── smoke_test.ps1    # Post-deploy smoke test
├── tests/
│   ├── conftest.py
│   ├── test_evaluator.py
│   ├── test_policy_loader.py
│   ├── test_audit.py
│   ├── test_hitl.py
│   ├── test_main.py
│   └── test_harness.py
├── template.yaml         # SAM deployment template
├── deploy.ps1            # Deployment script
├── .env.example          # Environment variable template
└── requirements.txt
```

## Production Considerations

This project is deployed on Hugging Face Spaces free tier (Docker + MongoDB
Atlas M0) and AWS Lambda free tier (DynamoDB + API Gateway). The following
hardening measures are implemented:

| Feature | Implementation | Details |
|---------|---------------|---------|
| **API key authentication** | `X-API-Key` header checked in middleware | Applied to `/evaluate`, `/hitl/*`, `/audit-log`, `/policies`. `/health`, `/docs`, `/static`, and `/` are public. |
| **Rate limiting** | In-memory sliding window, 60 req/min per key | Returns `429 Too Many Requests` with `Retry-After` header when exceeded. Sliding window per unique `X-API-Key` value. Applies only to `/evaluate`. In-memory store resets on container restart — acceptable for single-container deployment. |
| **Correlation IDs** | `X-Request-ID` header | Generated as UUID if not provided by caller. Returned in response headers and present in all structured log output. Every `EVALUATE` log line includes `request_id=...`. |
| **Request size limit** | 100KB content-length check | `413 Payload Too Large` returned before any processing for oversized requests. Applied in middleware before routing. |
| **Graceful degradation** | Audit/HITL writes wrapped in try/except | If MongoDB is unreachable, `/evaluate` still returns a policy decision (evaluator doesn't need DB). `audit_written=false` is flagged in the response. WARNING-level logs capture the failure detail for manual recovery. |
| **Dashboard** | Self-contained `dashboard.html` | No external dependencies, no build step. API key is held in a page-scoped JS variable (not `localStorage`). Auto-refreshes HITL queue every 5s, health every 15s. Shows a warning banner if DB is unreachable. |

### What a larger-scale production deployment would add next

| Area | Next step |
|------|-----------|
| **API key rotation** | Real key rotation with overlapping validity periods (e.g. two active keys, deprecate old after rotation window). Currently a single static key. |
| **Persistent rate limiting** | Redis or MongoDB-backed rate counter so limits survive container restarts. Currently in-memory only. |
| **Multi-region DB** | MongoDB Atlas M10+ with replica sets for HA and cross-region reads. Currently single-region M0. |
| **Dedicated observability** | Datadog / Grafana / Prometheus for metrics (latency histograms, error rates, rate-limit hit counts). Currently only HF container logs. |
| **Autoscaling** | Kubernetes or multiple HF Spaces replicas behind a load balancer. Currently single container. |
| **Audit reconciliation** | Background queue (e.g. Celery + Redis) for retrying failed audit writes. Currently logs to WARNING only. |
| **Webhook notifications** | Outbound webhooks on HITL creation/resolution. Currently requires polling `GET /hitl/{id}`. |
| **RBAC / multi-tenant** | Isolated rule sets per tenant, scoped API keys with read/write/admin roles. Currently single-tenant. |

