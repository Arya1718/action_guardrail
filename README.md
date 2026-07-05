---
title: Action Guardrail
emoji: 🛡️
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
---

# Action Guardrail

[![test](https://github.com/Arya1718/action_guardrail/actions/workflows/test.yml/badge.svg)](https://github.com/Arya1718/action_guardrail/actions/workflows/test.yml)

> **Live demo (HF Spaces)**: https://AntiSpiral18-action-guardrail.hf.space
> **AWS deployment**: https://q6mucicr0e.execute-api.us-east-1.amazonaws.com

A policy engine for AI agents that intercepts tool calls **before** execution and
evaluates them against declarative rules. Built with FastAPI for local development,
deployed on Hugging Face Spaces and AWS Lambda (both always-free tier).

## Quick Start — 3 Ways

### 1. Against the live HF Space (no install needed)

```bash
curl -s -H "X-API-Key: AryaGuardrail1804" \
  https://AntiSpiral18-action-guardrail.hf.space/health | python3 -m json.tool

curl -s -X POST https://AntiSpiral18-action-guardrail.hf.space/evaluate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: AryaGuardrail1804" \
  -d '{"tool_call":{"tool":"delete_records","parameters":{"record_count":500}},"dry_run":false}' | python3 -m json.tool
```

### 2. Against the AWS deployment

```bash
curl -s -H "X-API-Key: AryaGuardrail1804" \
  https://q6mucicr0e.execute-api.us-east-1.amazonaws.com/health | python3 -m json.tool
```

### 3. Local development server

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

### Install the package (for scripts and MCP server)

```bash
pip install -e .
pytest -v        # 75+ tests must pass
```

## Scripts

| Script | Purpose | Example |
|--------|---------|---------|
| `harness/run_all.py` | Run 4 policy scenarios against any guardrail endpoint | `GUARDRAIL_API_URL=... GUARDRAIL_API_KEY=... python harness/run_all.py --auto-approve` |
| `scripts/review_pending.py` | Interactive HITL reviewer (approve/reject pending reviews) | `python scripts/review_pending.py --watch` |
| `harness/run_scenarios.py` | Run scenarios individually | `python harness/run_scenarios.py` |
| `mcp_server/test_server.py` | End-to-end MCP server test | `python mcp_server/test_server.py` |
| `deploy/smoke_test_aws.ps1` | Smoke test the AWS deployment | `.\deploy\smoke_test_aws.ps1 -Endpoint "https://..." -ApiKey "..."` |

### Two-terminal HITL demo (requires `.env`)

```bash
# Terminal 1 — watch for pending reviews
python scripts/review_pending.py --watch

# Terminal 2 — run scenarios (pauses on require_hitl)
python harness/run_all.py --no-auto-approve
```

## Dashboard

Open **https://AntiSpiral18-action-guardrail.hf.space** (or `/dashboard`) in a
browser. Enter `AryaGuardrail1804` as the API key, click **Connect**, and browse
the audit log. You can also ask natural-language questions about the log data
via the *Ask Groq* section.

## Using in AWS CloudShell

Clone the repo and run directly:

```bash
git clone -b feature/infrastructure-upgrade https://github.com/Arya1718/action_guardrail.git
cd action_guardrail
pip install -e .
export GUARDRAIL_API_URL=https://q6mucicr0e.execute-api.us-east-1.amazonaws.com
export GUARDRAIL_API_KEY=AryaGuardrail1804
export GROQ_API_KEY=your-groq-key  # only needed for run_all.py
python scripts/review_pending.py
```

To keep scripts running after closing the terminal, use `tmux`:

```bash
tmux new -s guardrail
python scripts/review_pending.py --watch   # Ctrl+B then D to detach
tmux attach -t guardrail                    # reattach later
```

## Environment Variables

Create a `.env` file or export them directly. See `docs/environment.md` for the
full reference.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `API_KEY` | No | `dev-placeholder-key` | Server-side master API key |
| `GUARDRAIL_API_URL` | For scripts | `http://localhost:8001` | Guardrail endpoint the harness/reviewer talks to |
| `GUARDRAIL_API_KEY` | For scripts | falls back to `API_KEY` | API key for the harness/reviewer |
| `GROQ_API_KEY` | For harness | — | Groq API key (free at console.groq.com) |
| `STORAGE_BACKEND` | No | `memory` | `memory`, `mongo`, or `dynamodb` |

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

`StorageBackend` is an abstract base class with `InMemoryStorage` (local dev,
tests), `MongoStorage` (HF Spaces, AWS Lambda), and `DynamoDBStorage`
(AWS Lambda). Swap by changing `STORAGE_BACKEND` env var.

## Alternate Deployment: AWS (always-free tier)

A second, independent parallel deployment targeting AWS Lambda + API Gateway
HTTP API. Uses the **same MongoDB Atlas** cluster as the HF Spaces deployment
but a **separate database name** (`guardrail_aws`) so audit data stays
completely isolated.

### Architecture

```
Agent / Harness  ──HTTPS──>  API Gateway HTTP API  ──>  Lambda (container)
                                 │                           │
                                 └── SSM Parameter Store      │
                                     (secrets)          MongoStorage
                                                        (guardrail_aws DB)
```

All within [AWS Always-Free Tier](https://aws.amazon.com/free/) limits:
Lambda (1M requests + 400K GB-seconds forever), API Gateway HTTP API (1M
calls for 12 months, ~$1/M after), SSM Standard parameters (10K free),
CloudWatch Logs (5 GB free first month, ~$0.50/GB after).

### Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| [AWS CLI](https://aws.amazon.com/cli/) | 2.x | `pip install awscli` or `choco install awscli` |
| [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) | 1.x+ | `pip install aws-sam-cli` |
| Docker | Desktop | Required by `sam build` for container image |

Configure the AWS CLI with credentials that have sufficient permissions
(AdministratorAccess or a scoped policy covering CloudFormation, ECR, Lambda,
API Gateway, SSM, IAM, and CloudWatch Logs). Confirm with:

```bash
aws sts get-caller-identity
```

### Deploy

```powershell
# First deployment (guided prompts)
.\deploy-aws.ps1

# Subsequent deployments (uses saved samconfig.toml)
.\deploy-aws.ps1 -DeployOnly
```

**Guided deploy prompts — what to expect:**

| Prompt | Recommended value |
|--------|-------------------|
| Stack Name | `action-guardrail-aws` |
| AWS Region | `us-east-1` |
| Parameter ApiKeyParamValue | A random string (e.g. `pwgen -s 32 1`) |
| Parameter MongoUriParamValue | Your MongoDB Atlas connection string |
| Parameter MongoDbNameParamValue | `guardrail_aws` (default, keeps data separate from HF) |
| Parameter GroqApiKeyParamValue | (optional) Groq API key for `/query` |
| Confirm changes before deploy | `Y` |
| Allow SAM CLI IAM role creation | `Y` |
| Disable rollback | `N` |
| Save arguments to samconfig.toml | `Y` |

After deploy completes, get the URL:

```bash
sam list stack-outputs --stack-name action-guardrail-aws
```

### Smoke test

```powershell
.\deploy\smoke_test_aws.ps1 -Endpoint "https://abc123.execute-api.us-east-1.amazonaws.com" -ApiKey "your-api-key"
```

Expected output:
```
=== Smoke Test: https://abc123.execute-api.us-east-1.amazonaws.com ===
  [PASS] GET /health
  [PASS] GET / (landing)
  [PASS] POST /evaluate -> block (bulk delete)
  [PASS] POST /evaluate -> require_hitl (external email)
  [PASS] POST /evaluate -> log_and_allow (confidential file)
  [PASS] POST /evaluate -> allow (no matching rule)

+-------------------+
| ALL 6 PASSED  ✓ |
+-------------------+
```

### Run the harness against the AWS endpoint

```powershell
$env:GUARDRAIL_API_URL = "https://abc123.execute-api.us-east-1.amazonaws.com"
$env:GUARDRAIL_API_KEY = "your-api-key"
python harness\run_all.py
```

### Free-tier cost breakdown

| Service | Free tier limit | Monthly usage at 10k evaluations | Charges |
|---------|----------------|----------------------------------|---------|
| **Lambda** | 1M requests + 400,000 GB-seconds (forever) | ~10k requests × 0.5 GB × 0.1s = 500 GB-seconds | **$0** |
| **API Gateway HTTP API** | 1M calls (12 months) | 10k calls | **$0 (~$0.01 after 12mo)** |
| **ECR storage** | 500 MB (always-free) | ~300 MB image | **$0** |
| **SSM Parameter Store** | 10,000 parameters (standard) | 4 parameters | **$0** |
| **CloudWatch Logs** | 5 GB ingestion (first month) | ~1 MB | **$0** |
| **MongoDB Atlas M0** | 512 MB storage (free tier) | ~1 MB | **$0** |
| **Total** | | | **$0/month** |

### One-time cost note

API Gateway's 1M-call free tier expires after 12 months. Beyond that:
- **API Gateway**: ~$1.00 per million requests
- **Lambda**: For this low traffic, still $0 under the 1M request forever tier
- **CloudWatch Logs**: ~$0.50 per GB ingested
- Everything else remains free indefinitely

### Project Layout

```
guardrail/
├── app/
│   ├── main.py           # FastAPI app + routes
│   ├── lambda_handler.py # Lambda entrypoint (Mangum)
│   ├── models.py         # Pydantic models
│   ├── evaluator.py      # Core evaluation logic
│   ├── policy_loader.py  # YAML rule loader
│   ├── storage.py        # Storage interface + InMemory / DynamoDB / Mongo
│   ├── audit.py          # Audit log logic
│   ├── hitl.py           # HITL queue logic
│   ├── notifications.py  # Slack webhook notifier
│   └── config.py         # Settings (env / SSM)
├── mcp_server/
│   ├── server.py         # MCP server (Claude Desktop integration)
│   ├── test_server.py    # MCP server test script
│   └── README.md         # MCP server documentation
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
│   ├── smoke_test.ps1        # Post-deploy smoke test (HF Spaces)
│   └── smoke_test_aws.ps1    # Post-deploy smoke test (AWS)
├── tests/
│   ├── conftest.py
│   ├── test_evaluator.py
│   ├── test_policy_loader.py
│   ├── test_audit.py
│   ├── test_hitl.py
│   ├── test_main.py
│   ├── test_harness.py
│   ├── test_notifications.py
│   └── test_multi_tenancy.py
├── template.yaml         # SAM deployment template (AWS Lambda)
├── Dockerfile.lambda     # Lambda container image build (separate from HF Dockerfile)
├── deploy.ps1            # Deployment script (HF Spaces / old SAM)
├── deploy-aws.ps1        # Deployment script (AWS, new container-based)
├── pyproject.toml        # Package build config (src + mcp_server)
├── .env.example          # Environment variable template
└── requirements.txt
```

## MCP Server (Claude Desktop Integration)

A [Model Context Protocol](https://modelcontextprotocol.io) server exposes
guardrail tools directly to Claude Desktop.

### Install

```bash
pip install -e .
```

### Claude Desktop config

Find your Python path:
```bash
# Windows
where python
# macOS/Linux
which python
```

Add to `claude_desktop_config.json` (Windows: `%APPDATA%\Claude\`, macOS:
`~/Library/Application Support/Claude/`):

```json
{
  "mcpServers": {
    "action-guardrail": {
      "command": "C:\\Path\\To\\python.exe",
      "args": ["-m", "mcp_server.server"]
    }
  }
}
```

Restart Claude Desktop, set tool-loading mode to **"Tools already loaded"**.

### Available tools

| Tool | Description |
|------|-------------|
| `evaluate_action` | Check a proposed tool call against policy |
| `list_pending_reviews` | List all pending HITL review requests |
| `approve_review` | Approve a pending review |
| `reject_review` | Reject a pending review |

See `mcp_server/README.md` for full details.

## Slack Notifications

Set `SLACK_WEBHOOK_URL` in `.env` to receive real-time Slack alerts when the
guardrail blocks an action or requires human review. The message includes the
tool name, outcome, matched rule, and reason. Silent on `allow`/`log_and_allow`
outcomes. Failures are logged at WARNING level — never crash the request.

## Multi-tenancy

Set `ORG_API_KEYS` in `.env` to a JSON mapping of org_id → API key:

```
ORG_API_KEYS={"acme":"key-acme-123","globex":"key-globex-456"}
```

- **Master key** (`API_KEY` env var) has cross-org access to all records.
- **Org keys** scope data access to that org's audit records and HITL requests.
- Leave `ORG_API_KEYS` empty for single-tenant mode (backward compatible).

## Production Considerations

This project is deployed on Hugging Face Spaces free tier (Docker + MongoDB
Atlas M0) and AWS Lambda free tier (DynamoDB + API Gateway). The following
hardening measures are implemented:

| Feature | Implementation | Details |
|---------|---------------|---------|
| **API key authentication** | `X-API-Key` header checked in middleware | Applied to `/evaluate`, `/hitl/*`, `/audit-log`, `/policies`. `/health`, `/docs`, and `/` are public. Supports multi-tenant key mapping via `ORG_API_KEYS`. |
| **Rate limiting** | In-memory sliding window, 60 req/min per key | Returns `429 Too Many Requests` with `Retry-After` header when exceeded. Sliding window per unique `X-API-Key` value. Applies only to `/evaluate`. In-memory store resets on container restart — acceptable for single-container deployment. |
| **Correlation IDs** | `X-Request-ID` header | Generated as UUID if not provided by caller. Returned in response headers and present in all structured log output. Every `EVALUATE` log line includes `request_id=...`. |
| **Request size limit** | 100KB content-length check | `413 Payload Too Large` returned before any processing for oversized requests. Applied in middleware before routing. |
| **Graceful degradation** | Audit/HITL writes wrapped in try/except | If MongoDB is unreachable, `/evaluate` still returns a policy decision (evaluator doesn't need DB). `audit_written=false` is flagged in the response. WARNING-level logs capture the failure detail for manual recovery. |
| **Slack notifications** | `app/notifications.py` sends async Slack webhooks on block/require_hitl | Fire-and-forget via `httpx.AsyncClient`. WARNING-level log on failure, never crashes the request. |
| **Multi-tenancy** | `resolve_org_id()` in `config.py` maps API keys to orgs | Admin key sees all orgs; org keys are scoped. `org_id` stored on every audit record and HITL request. |

### What a larger-scale production deployment would add next

| Area | Next step |
|------|-----------|
| **API key rotation** | Real key rotation with overlapping validity periods (e.g. two active keys, deprecate old after rotation window). Currently a single static key. |
| **Persistent rate limiting** | Redis or MongoDB-backed rate counter so limits survive container restarts. Currently in-memory only. |
| **Multi-region DB** | MongoDB Atlas M10+ with replica sets for HA and cross-region reads. Currently single-region M0. |
| **Dedicated observability** | Datadog / Grafana / Prometheus for metrics (latency histograms, error rates, rate-limit hit counts). Currently only HF container logs. |
| **Autoscaling** | Kubernetes or multiple HF Spaces replicas behind a load balancer. Currently single container. |
| **Audit reconciliation** | Background queue (e.g. Celery + Redis) for retrying failed audit writes. Currently logs to WARNING only. |
| **Org-scoped policies** | Isolated rule sets per tenant. Currently global rules apply to all orgs. |

