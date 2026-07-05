# Runbook

## Quick Start (Docker)

```bash
cd guardrail/

# Build and start the API server
docker compose up --build

# Or with a custom API key:
API_KEY=my-secret-key docker compose up --build
```

The service starts at `http://localhost:7860`.

## Quick Start (No Docker)

```bash
cd guardrail/
pip install -r requirements.txt
uvicorn app.main:app --reload --port 7860
```

## MCP Server (Claude Desktop)

```bash
cd guardrail/
pip install -e .
python -m mcp_server.server
```

Runs on stdio (no port). See `mcp_server/README.md` for Claude Desktop setup.

## Verify It's Running

```bash
# Root — should return clean JSON
curl -s http://localhost:7860/

# Health — public, no API key needed
curl -s http://localhost:7860/health | python -m json.tool

# Evaluate — requires X-API-Key header
curl -s -X POST http://localhost:7860/evaluate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-placeholder-key" \
  -d '{"tool_call": {"tool": "delete_records", "parameters": {"record_count": 500}}}' \
  | python -m json.tool

# Swagger UI — interactive docs
open http://localhost:7860/docs
```

## Stop the Service

```bash
docker compose down
```

Or press Ctrl+C in the terminal running uvicorn.

## Check Logs

```bash
# With Docker
docker compose logs -f

# Without Docker — logs go to stdout
```

All logs are structured JSON. Example:

```json
{"timestamp": "2026-07-02T16:30:00.123456+00:00", "level": "INFO", "name": "app.main", "message": "EVALUATE request_id=abc-123 tool=delete_records ..."}
```

## Run Tests

```bash
cd guardrail/
pytest -v
```

Expect 75 tests, all passing.

## Common Troubleshooting

### "DB shows unreachable" on /health

```
"database": "unreachable"
```

1. Check `MONGO_URI` in `.env` — it must be a valid MongoDB Atlas connection string.
2. Verify the Atlas cluster IP whitelist allows connections (set to `0.0.0.0/0`
   for free tier).
3. If running locally without Mongo, set `STORAGE_BACKEND=memory` (the default).

### "401 on all endpoints"

```
{"error": "Unauthorized", "detail": "Missing or invalid X-API-Key header"}
```

1. Check `API_KEY` in `.env` matches the value in your `X-API-Key` header.
2. If using multi-tenancy (`ORG_API_KEYS`), make sure the key matches one of
   the org keys or the master `API_KEY`.
3. The default key is `dev-placeholder-key` if `API_KEY` is not set.
4. Health check (`GET /health`) and docs (`GET /docs`) do not require a key.

### "429 Too Many Requests"

```
{"error": "Too Many Requests", "detail": "Rate limit exceeded. Try again in 42s."}
```

Wait the `Retry-After` seconds before sending another `/evaluate` request.
The limit is 60 requests per minute per API key. Resets on container restart.

### "413 Payload Too Large"

Reduce the request body size. Limit is 100KB.

### MCP server: "No module named 'mcp_server'"

The `mcp_server/` package is not installed. Run:

```bash
pip install -e .
```

This registers it so `python -m mcp_server.server` works from any directory.

### MCP server: "Server disconnected" in Claude Desktop

1. Run `python -m mcp_server.server` directly in a terminal to check for errors.
2. Ensure `pip install -e .` was run so the package is importable.
3. In Claude Desktop, set tool-loading mode to **"Tools already loaded"**.
4. Check Claude Desktop logs (click **View Logs** on the server card).

### Container exits immediately

1. Check if port 7860 is already in use: `netstat -ano | findstr :7860`
2. If running Docker Desktop, ensure it is running and has resources allocated.
3. Try `docker compose logs` to see the startup error.

### Cannot connect to the running server

1. Ensure the server started successfully (check logs for `Uvicorn running on ...`).
2. Confirm you are hitting the correct port (default: 7860, override with
   `--port` or docker-compose port mapping).
3. If using Docker, the port must be published: `docker run -p 7860:7860 ...`

## AWS CloudShell Usage

AWS CloudShell (https://console.aws.amazon.com/cloudshell/) is a free browser-
based terminal. Use it to run the HITL reviewer or scenarios against the
live AWS deployment:

```bash
# Clone the repo (feature branch has the latest)
git clone -b feature/infrastructure-upgrade https://github.com/Arya1718/action_guardrail.git
cd action_guardrail

# Install dependencies
pip install -e .

# Set environment variables
export GUARDRAIL_API_URL=https://q6mucicr0e.execute-api.us-east-1.amazonaws.com
export GUARDRAIL_API_KEY=AryaGuardrail1804

# Run the HITL reviewer
python scripts/review_pending.py

# Or run all scenarios
export GROQ_API_KEY=gsk_...  # optional, only needed for scenario 4
python harness/run_all.py --auto-approve
```

### Keep scripts running after terminal closes

Use `tmux` (pre-installed in CloudShell):

```bash
# Start a session
tmux new -s guardrail

# Inside tmux: run your script
python scripts/review_pending.py --watch

# Detach: Ctrl+B then D
# Reattach later:
tmux attach -t guardrail
```

Or use `nohup`:

```bash
nohup python scripts/review_pending.py --watch > reviewer.log 2>&1 &
tail -f reviewer.log
```
