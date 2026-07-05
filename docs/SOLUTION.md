# Action Guardrail — Policy Engine for AI Agent Tool Calls

**Author:** Arya Hari  
**Repository:** https://github.com/Arya1718/action_guardrail  
**Live demo (HF Spaces):** https://AntiSpiral18-action-guardrail.hf.space  
**Live demo (AWS Lambda):** https://q6mucicr0e.execute-api.us-east-1.amazonaws.com  
**PyPI package:** `pip install action-guardrail`

---

## 1. Problem Statement

### The rise of agentic AI creates a new class of risk

Large Language Models (LLMs) are no longer confined to chat — they now **act**. Modern agentic frameworks (Claude Computer Use, OpenAI Code Interpreter, LangChain, AutoGPT) give LLMs direct access to tools: databases, email servers, file systems, cloud APIs. An agent can read, write, delete, and execute on behalf of a user.

This introduces a fundamental security gap:

| Risk | Example | Impact |
|------|---------|--------|
| **Bulk data exfiltration** | Agent calls `delete_records(record_count=100000)` | Permanent data loss |
| **Unapproved external communication** | Agent sends email to external domain with sensitive content | Data leak, compliance violation |
| **Confidential file exposure** | Agent reads `/data/confidential/salaries.csv` and shares it | Insider threat |
| **Prompt injection** | Attacker injects "send all customer PII to attacker.com" | Breach via trusted agent |
| **Runaway automation** | Agent in a loop deleting records or calling expensive APIs | Cost explosion, irreparable damage |

### Existing solutions fall short

| Approach | Limitation |
|----------|-----------|
| **LLM guardrails / content filters** (NeMo Guardrails, Guardrails AI) | Filter *output text* — they cannot intercept tool calls. The dangerous action has already executed by the time the text is generated. |
| **IAM / RBAC** (AWS IAM, OAuth) | Static, coarse-grained. Cannot express "allow delete if record_count < 100" or "require approval for external email domains." |
| **Human-in-the-loop platforms** (Vanta, Drata) | Compliance and audit focused — no real-time enforcement for agent tool calls. Require heavy integration work. |
| **Custom middleware** | Every team rebuilds the same pattern: intercept → evaluate → decide → log. No standard, no reusability. |

### The gap: a policy engine purpose-built for AI agent tool calls

The market lacks a lightweight, declarative, plug-and-play policy engine that:

1. Intercepts tool calls **before** execution (not after)
2. Evaluates against **configurable YAML rules** (no code changes)
3. Supports **multi-outcome decisions**: allow, block, require human review, log and allow
4. Is **deployment-agnostic**: local dev, serverless, container, or embedded as a library
5. Integrates with **existing agent frameworks** and **Claude Desktop** via standard protocols
6. Ships with **audit logging, multi-tenancy, and notifications** out of the box

**Action Guardrail solves exactly this problem.**

---

## 2. Solution Overview

Action Guardrail is a policy evaluation engine that sits between an AI agent and its tools, intercepting every proposed tool call and evaluating it against declarative YAML rules **before** the tool executes.

### Core workflow

```
Agent proposes tool call  ──▶  Guardrail evaluates     ──▶  Decision returned
  (e.g. "delete 500 records")    against YAML rules          (block / allow /
                                    in priority order         require_hitl /
                                                              log_and_allow)
```

### Decision outcomes

| Outcome | Effect |
|---------|--------|
| `allow` | Tool executes normally |
| `block` | Tool is denied, agent receives explanation |
| `require_hitl` | Action paused, pending review created, human must approve/reject |
| `log_and_allow` | Tool executes, but the action is permanently recorded |

### One-line install

```bash
pip install action-guardrail
```

```python
from action_guardrail.evaluator import evaluate_action
from action_guardrail.policy_loader import load_policies

rules = load_policies("policies.yaml")
decision = evaluate_action(
    tool_call={"tool": "delete_records", "parameters": {"record_count": 500}},
    rules=rules,
)
print(decision.outcome)  # "block"
```

---

## 3. Architecture

### High-level diagram

```
                              ┌─────────────────────────────┐
                              │      Agent / Simulator       │
                              │  (LLM, script, Claude, etc.)│
                              └───────────┬─────────────────┘
                                          │ POST /evaluate
                                          ▼
                     ┌─────────────────────────────────────────┐
                     │         FastAPI Gateway (app/main.py)    │
                     │                                          │
                     │  ┌──────────┐  ┌──────────┐  ┌────────┐  │
                     │  │API Key   │  │Rate      │  │Request │  │
                     │  │Auth      │  │Limiter   │  │ID Gen  │  │
                     │  │(org-     │  │(60/min   │  │(UUID)  │  │
                     │  │ scoped)  │  │ per key) │  │        │  │
                     │  └──────────┘  └──────────┘  └────────┘  │
                     └──────────────────┬──────────────────────┘
                                        │
              ┌─────────────────────────┼─────────────────────────┐
              │                         │                         │
              ▼                         ▼                         ▼
   ┌──────────────────┐    ┌──────────────────────┐    ┌──────────────────┐
   │  Policy Loader   │    │     Evaluator         │    │  Decision Handler│
   │  (app/policy_    │    │  (app/evaluator.py)   │    │  (app/main.py)   │
   │   loader.py)     │    │                      │    │                  │
   │                  │    │  Iterates rules in    │    │  ┌────────────┐  │
   │  Reads YAML      │    │  priority order.      │    │  │Audit Log  │  │
   │  Sorts by        │───▶│  First match wins.    │───▶│  │  Write    │  │
   │  priority        │    │  Conditions: eq, gt,  │    │  ├────────────┤  │
   │  Caches at       │    │  contains, regex,     │    │  │HITL Queue │  │
   │  startup         │    │  in, not_in...        │    │  │  Manage   │  │
   └──────────────────┘    └──────────────────────┘    │  ├────────────┤  │
                                                        │  │Notifications│  │
                                                        │  │  (Slack)   │  │
                                                        │  └────────────┘  │
                                                        └──────────────────┘
                                                                  │
                                      ┌───────────────────────────┼───────────────┐
                                      │                           │               │
                                      ▼                           ▼               ▼
                           ┌──────────────────┐       ┌────────────────┐  ┌──────────────┐
                           │   Storage Backend │       │  HITL Requests │  │  Slack       │
                           │  (Abstract)      │       │  (pending/     │  │  Webhook     │
                           │                  │       │   resolved)    │  │  (async)     │
                           │  ┌────────────┐  │       └────────────────┘  └──────────────┘
                           │  │InMemory   │  │
                           │  ├────────────┤  │
                           │  │Mongo      │  │
                           │  ├────────────┤  │
                           │  │DynamoDB   │  │
                           │  └────────────┘  │
                           └──────────────────┘
```

### Three entry points

All three share the same evaluation engine, policy files, and storage backend:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Action Guardrail                                │
│                                                                     │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │   FastAPI Server  │  │   MCP Server     │  │   AWS Lambda     │  │
│  │  (HF Spaces /     │  │  (Claude Desktop,│  │  (API Gateway    │  │
│  │   Local dev)      │  │   any MCP client)│  │   HTTP API)      │  │
│  │                   │  │                  │  │                  │  │
│  │  Port 7860/8000   │  │  stdio JSON-RPC  │  │  Mangum adapter  │  │
│  │  REST API         │  │  4 MCP tools     │  │  Container image │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │              Shared Core (evaluator, policies, storage)      │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Component breakdown

**FastAPI Gateway** (`app/main.py`)
- Routes: `/evaluate`, `/health`, `/hitl/*`, `/audit-log*`, `/query`, `/dashboard`
- Middleware: API key auth, rate limiting (sliding window, 60/min/key), request size limit (100 KB), correlation ID (X-Request-ID)
- Multi-tenancy: org_id resolved from API key, scoped to all queries

**Policy Loader** (`app/policy_loader.py`)
- Reads YAML → validates → sorts by priority
- Single function: `load_policies(path) → list[Rule]`

**Evaluator** (`app/evaluator.py`)
- Pure function: `evaluate_action(tool_call, rules) → Decision`
- First-match-wins semantics
- Condition operators: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `contains`, `regex`
- Zero dependencies on storage, network, or any I/O

**Decision Handler** (inline in `app/main.py`)
- Translates Decision into side effects:
  - `block` → return 403-like response, write audit
  - `require_hitl` → create pending HITL request, notify Slack, write audit
  - `log_and_allow` / `allow` → write audit, return success
  - `dry_run=true` override → always allow, but store original intended decision

**Storage Backend** (`app/storage.py`)
- Abstract `StorageBackend` with three implementations:
  - `InMemoryStorage` — local dev / tests, no persistence
  - `MongoStorage` — HF Spaces, general production
  - `DynamoDBStorage` — AWS Lambda (legacy SAM deployment)
- Swap via `STORAGE_BACKEND` env var — no code changes

**Audit Log** (`app/audit.py`)
- Records: id, tool, outcome, dry_run, reason, matched_rule_id, org_id, created_at
- Queryable by tool, outcome, time range, org_id
- Aggregate summary, CSV export

**HITL Queue** (`app/hitl.py`)
- Create, list (with org scoping), approve, reject
- Polled by the agent harness (`guardrail_client.py`)
- Resolved via API, CLI (`scripts/review_pending.py`), or MCP tools

**Notifications** (`app/notifications.py`)
- Async Slack webhook via `httpx.AsyncClient`
- Fires on block and require_hitl outcomes
- Fire-and-forget — never blocks or crashes the request

---

## 4. How It Works — Step by Step

### Rule evaluation flow

```
Tool call arrives:
  tool="delete_records", parameters={"record_count": 500}

1. Load rules (cached at startup, priority-sorted):
   ┌─────────────────────────────────────────────────────────────┐
   │ Priority 10: block-bulk-delete  (tool=delete_records,       │
   │               condition: record_count > 100)                │
   │ Priority 10: hitl-external-email (tool=send_email,          │
   │               condition: recipient_domain not in [mycompany])│
   │ Priority 10: log-confidential-read (tool=read_file,         │
   │               condition: path contains "confidential")      │
   └─────────────────────────────────────────────────────────────┘

2. Iterate rules in priority order:

   Rule 1: block-bulk-delete
     tool match? delete_records == delete_records ✓
     condition: record_count(500) > 100? true ✓
     → MATCH. Outcome: block.

   Decision returned:
   {
     "outcome": "block",
     "matched_rule_id": "block-bulk-delete",
     "reason": "Delete of 500 records exceeds the maximum of 100",
     "action": "block"
   }

3. Side effects:
   - Audit record written with outcome "block"
   - Slack notification sent (if configured)
```

### Human-in-the-loop flow

```
Tool call: send_email(recipient_domain="gmail.com")

1. Evaluator matches rule "hitl-external-email"
2. Decision: require_hitl
3. A HitlRequest is created in storage with status "pending"
4. Response includes hitl_request_id
5. Agent polls GET /hitl/{id} every 3s

Meanwhile, in another terminal:
  python scripts/review_pending.py --watch
  → Displays: "Pending Review: send_email to gmail.com"
  → Human types "a" (approve) + name
  → POST /hitl/{id}/approve resolves the request

Agent detects status change, executes the tool.
```

---

## 5. Market Comparison

| Feature | Action Guardrail | NeMo Guardrails | Guardrails AI | Custom middleware |
|---------|-----------------|-----------------|---------------|-------------------|
| **Tool-call interception** | ✅ Native | ❌ Text only | ❌ Text only | ⚠️ Manual |
| **Declarative YAML rules** | ✅ Built-in | ✅ Colang | ❌ Python code | ⚠️ Must build |
| **Human-in-the-loop** | ✅ Built-in queue + CLI + MCP | ❌ | ❌ | ⚠️ Must build |
| **Multi-outcome decisions** | ✅ block / allow / require_hitl / log_and_allow | ❌ Block only | ❌ Block only | ⚠️ Must build |
| **Audit log** | ✅ Built-in, queryable, exportable | ❌ | ❌ | ⚠️ Must build |
| **Multi-tenancy** | ✅ Built-in (org-scoped keys) | ❌ | ❌ | ⚠️ Must build |
| **Slack notifications** | ✅ Built-in | ❌ | ❌ | ⚠️ Must build |
| **Pluggable storage** | ✅ Memory / MongoDB / DynamoDB | ❌ | ❌ | ⚠️ Must build |
| **MCP / Claude Desktop** | ✅ Native MCP server | ❌ | ❌ | ⚠️ Must build |
| **AWS Lambda deployment** | ✅ Container image + SAM | ❌ | ❌ | ⚠️ Must build |
| **Standalone PyPI package** | ✅ `pip install action-guardrail` | ✅ | ✅ | N/A |
| **Zero I/O evaluation** | ✅ Pure Python, no DB/net calls | ❌ | ❌ | Depends |
| **License** | MIT | Apache 2.0 | Apache 2.0 | Proprietary |

### Key differentiators

1. **Tool-call first, not text-first**: Most guardrail solutions filter LLM *output text*. Action Guardrail intercepts the *action itself* — the delete, the email, the file read — before it happens.

2. **Declarative policies, not code**: Rules are YAML files that non-engineers can read and modify. No Python, no SQL, no DSL to learn.

3. **Human-in-the-loop as a first-class primitive**: `require_hitl` is built into the evaluation engine, not bolted on. The review queue, CLI tool, and MCP tools all ship with the project.

4. **Deployment agnosticism**: Run as a FastAPI server, an MCP server on stdio, or a serverless Lambda. The core evaluation engine is also a standalone pip package with zero runtime dependencies.

5. **Storage pluggability**: InMemory for tests, MongoDB for production, DynamoDB for AWS-native — swap with one env var.

6. **Built for the always-free tier**: Both HF Spaces (Docker + MongoDB Atlas M0) and AWS Lambda (container image + API Gateway HTTP API + SSM) are fully functional on free-tier resources.

---

## 6. Deployments

### Deployment 1: Hugging Face Spaces (public demo)

**URL:** https://AntiSpiral18-action-guardrail.hf.space

| Component | Technology |
|-----------|-----------|
| Compute | HF Spaces Docker SDK (always-free CPU) |
| API | FastAPI on port 7860 |
| Storage | MongoDB Atlas M0 (free tier, 512 MB) |
| Auth | API key via `API_KEY` secret |
| Uptime | 24/7 |

**Health check:** `GET /health` returns `{"status":"ok","policies_loaded":3,"database":"connected"}`

### Deployment 2: AWS Lambda (always-free tier)

**URL:** https://q6mucicr0e.execute-api.us-east-1.amazonaws.com

| Component | Technology |
|-----------|-----------|
| Compute | AWS Lambda (container image via ECR) |
| API | API Gateway HTTP API |
| Storage | MongoDB Atlas M0 (separate `guardrail_aws` DB) |
| Secrets | SSM Parameter Store (standard tier) |
| Auth | API key sent via `X-API-Key` header |
| Deployment | SAM (`sam build` + `sam deploy`) |
| Cost | $0/month (all services on free tier) |

### Deployment 3: Local development

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Deployment 4: MCP Server (Claude Desktop)

```bash
pip install -e .
python -m mcp_server.server
```

Add to `claude_desktop_config.json` and restart Claude Desktop. Four tools available: `evaluate_action`, `list_pending_reviews`, `approve_review`, `reject_review`.

---

## 7. Production Readiness

### Implemented

| Area | Detail |
|------|--------|
| **Authentication** | `X-API-Key` header validation on all protected endpoints |
| **Multi-tenancy** | Org-scoped API keys; admin key has cross-org access |
| **Rate limiting** | 60 requests/minute/key, sliding window, 429 with Retry-After |
| **Correlation IDs** | `X-Request-ID` header (generated if not provided) |
| **Request size limit** | 100 KB max payload on `/evaluate` |
| **Graceful degradation** | Evaluator returns decisions even if MongoDB is unreachable |
| **Structured logging** | JSON logs with timestamp, level, request_id, and context |
| **CloudWatch** | AWS Lambda deployment sends logs to CloudWatch |
| **Slack notifications** | Async webhook on block/require_hitl (fire-and-forget) |
| **Audit log** | Every evaluation persisted; queryable, exportable as CSV |
| **PyPI package** | Core modules vendored; `pip install action-guardrail` |
| **CI/CD** | GitHub Actions: `pytest -v` + `docker build .` on push/PR to main |

### Future hardening (production scale)

| Area | Next step |
|------|-----------|
| **Key rotation** | Overlapping validity periods, two active keys |
| **Persistent rate limiting** | Redis or MongoDB-backed counters |
| **Multi-region DB** | MongoDB Atlas M10+ replica sets |
| **Observability** | Datadog / Grafana / Prometheus metrics |
| **Autoscaling** | Kubernetes or multiple HF Spaces replicas |
| **Audit reconciliation** | Background queue for retrying failed audit writes |
| **Org-scoped policies** | Isolated rule sets per tenant |

---

## 8. Portability

The core evaluation engine is packaged as a **standalone Python library** on PyPI:

```bash
pip install action-guardrail
```

```python
from action_guardrail.evaluator import evaluate_action
from action_guardrail.policy_loader import load_policies
from action_guardrail.models import Rule

rules = load_policies("my_policies.yaml")
decision = evaluate_action(
    tool_call={"tool": "delete_records", "parameters": {"record_count": 500}},
    rules=rules,
)
if decision.outcome == "block":
    print(f"Blocked by rule: {decision.matched_rule_id}")
```

### Zero runtime dependencies

The vendored core (`src/action_guardrail/`) requires only:
- Python 3.11+
- Pydantic v2

No web framework, no database driver, no network I/O. This makes it embeddable in any Python project — a Jupyter notebook, a CI/CD pipeline, a desktop app, or an AWS Lambda function that does not use the full server deployment.

---

## 9. Repository Structure

```
guardrail/
├── app/                    # FastAPI server application
│   ├── main.py             # Routes, middleware, decision handler
│   ├── evaluator.py        # Core policy evaluation engine
│   ├── policy_loader.py    # YAML rule loader
│   ├── models.py           # Pydantic models
│   ├── storage.py          # Storage abstraction + implementations
│   ├── audit.py            # Audit log logic
│   ├── hitl.py             # HITL queue logic
│   ├── notifications.py    # Slack webhook sender
│   ├── config.py           # Settings (env vars / SSM)
│   └── lambda_handler.py   # AWS Lambda entry point (Mangum)
├── mcp_server/             # MCP protocol server for Claude Desktop
│   ├── server.py           # 4 MCP tools
│   └── test_server.py      # End-to-end test
├── harness/                # Agent simulation and test scenarios
│   ├── agent.py            # LLM agent loop (Groq)
│   ├── scenarios.py        # 4 scripted test scenarios
│   ├── guardrail_client.py # API client for the harness
│   └── run_all.py          # Bootstrap + run all scenarios
├── scripts/
│   └── review_pending.py   # Interactive HITL reviewer CLI
├── src/action_guardrail/   # Vendored core (published to PyPI)
│   ├── evaluator.py
│   ├── policy_loader.py
│   └── models.py
├── policies/
│   └── example_rules.yaml  # 3 example rules
├── deploy/                 # Deployment and smoke test scripts
├── template.yaml           # AWS SAM deployment template
├── Dockerfile              # HF Spaces container image
├── Dockerfile.lambda       # AWS Lambda container image
├── pyproject.toml          # Package build configuration
└── requirements.txt
```

---

## 10. Quick Start

### Against the live API (no install)

```bash
curl -s -X POST https://q6mucicr0e.execute-api.us-east-1.amazonaws.com/evaluate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: AryaGuardrail1804" \
  -d '{"tool_call":{"tool":"delete_records","parameters":{"record_count":500}},"dry_run":false}'
```

### Install locally

```bash
git clone https://github.com/Arya1718/action_guardrail.git
cd action_guardrail
pip install -e .
pytest -v          # 75+ tests
```

### Install as a library

```bash
pip install action-guardrail
```

### Run the demo

```bash
# Terminal 1 — HITL reviewer
python scripts/review_pending.py --watch

# Terminal 2 — Run scenarios
python harness/run_all.py --no-auto-approve
```

---

## 11. Video Demo Script

See `docs/demo-plan.md` for the full 5-minute presentation script covering:

- **0:00-2:00** — Live implementation demo (block, require_hitl with approval, log_and_allow, allow, audit log)
- **2:00-4:00** — Architecture walkthrough (components, flow, storage abstraction, three entry points)
- **4:00-5:00** — Production readiness, portability (PyPI package), Claude Desktop MCP integration, AWS deployment
