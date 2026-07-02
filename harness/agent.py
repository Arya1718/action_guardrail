import json
import os
import time
from typing import Any, Optional

from dotenv import load_dotenv
from groq import Groq, APIConnectionError, APIStatusError, RateLimitError

from harness.guardrail_client import (
    GuardrailConnectionError,
    GuardrailResponseError,
    approve_hitl,
    evaluate,
    poll_hitl,
)
from harness.tools import TOOL_SCHEMAS, execute_tool

load_dotenv()

MODEL = "llama-3.3-70b-versatile"
_client_instance = None

_INDENT = "  "


def _log(step: str, msg: str, indent: int = 0) -> None:
    prefix = "|" + _INDENT * indent
    print(f"{prefix} [{step}] {msg}")


def _get_client() -> Groq:
    global _client_instance
    if _client_instance is not None:
        return _client_instance
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        raise RuntimeError(
            "GROQ_API_KEY is not set.\n"
            "  Create a .env file with:\n"
            "    GROQ_API_KEY=your-key-here\n"
            "  Get a free key at https://console.groq.com\n"
        )
    _client_instance = Groq(api_key=key)
    return _client_instance


def _build_guardrail_params(tool_name: str, tool_input: dict) -> dict:
    params = dict(tool_input)
    if tool_name == "send_email" and "recipient" in params:
        recipient = params["recipient"]
        if "@" in recipient:
            params["recipient_domain"] = recipient.split("@", 1)[1]
    return params


def _call_groq_with_retry(
    messages: list, tools: list
) -> Any:
    max_retries = 3
    delays = [2, 4, 8]

    for attempt in range(max_retries + 1):
        try:
            return _get_client().chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )
        except RateLimitError:
            if attempt >= max_retries:
                raise
            _log("GROQ", f"Rate limited, retrying in {delays[attempt]}s...", indent=1)
            time.sleep(delays[attempt])
        except APIConnectionError:
            if attempt >= max_retries:
                raise
            _log("GROQ", f"Connection error, retrying in {delays[attempt]}s...", indent=1)
            time.sleep(delays[attempt])
        except APIStatusError as exc:
            if 400 <= exc.status_code < 500:
                raise
            if attempt >= max_retries:
                raise
            _log(
                "GROQ", f"Server error {exc.status_code}, retrying in {delays[attempt]}s...",
                indent=1,
            )
            time.sleep(delays[attempt])

    raise RuntimeError("Exhausted retries for Groq API call")


def run_agent_turn(
    user_prompt: str,
    dry_run: bool = False,
) -> dict:
    """
    Run a single agent conversation with Groq (llama-3.3-70b-versatile).
    Intercepts every tool call through the guardrail /evaluate endpoint
    before execution.

    Returns a summary dict with keys: prompt, outcome, matched_rule,
    final_text, audit_written.
    """
    print()
    print("+--------------------------------------------------------------------+")
    print(f"| AGENT SESSION [{MODEL}]")
    print(f"| Prompt: {user_prompt[:100]}")
    if dry_run:
        print("| Mode:   DRY RUN")
    print("+--------------------------------------------------------------------+")

    tools = TOOL_SCHEMAS

    tool_call_outcomes: list[dict] = []
    last_outcome: Optional[str] = None
    last_rule: Optional[str] = None
    audit_written = False
    final_text = ""

    messages: list[dict] = [
        {"role": "user", "content": user_prompt}
    ]
    turn = 0

    while True:
        turn += 1
        _log("GROQ", f"Sending to Groq (turn {turn})...", indent=1)

        response = _call_groq_with_retry(messages, tools)
        choice = response.choices[0]
        msg = choice.message

        if msg.content:
            final_text = msg.content

        tool_calls = msg.tool_calls

        if not tool_calls:
            _log("GROQ", "Response received (no more tool calls):", indent=1)
            for line in final_text.split("\n")[:5]:
                print(f"{_INDENT * 2}{line}")
            break

        # Append assistant message with tool_calls to history
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        }
        messages.append(assistant_msg)

        for tc in tool_calls:
            tool_name = tc.function.name
            tool_input = json.loads(tc.function.arguments)
            guardrail_params = _build_guardrail_params(tool_name, tool_input)

            _log(
                "PROPOSE",
                f"Groq wants to call '{tool_name}' params={tool_input}",
                indent=1,
            )

            gr_call = {
                "tool": tool_name,
                "parameters": guardrail_params,
                "agent_id": "harness",
                "session_id": f"scenario-{int(time.time())}",
            }

            try:
                gr_resp = evaluate(gr_call, dry_run=dry_run)
            except GuardrailConnectionError as e:
                _log("ERR", str(e), indent=1)
                raise
            except GuardrailResponseError as e:
                _log("ERR", f"Guardrail error: {e}", indent=1)
                raise

            outcome = gr_resp["outcome"]
            last_outcome = outcome
            last_rule = gr_resp.get("matched_rule_id")
            hitl_request_id = gr_resp.get("hitl_request_id")
            audit_written = True
            tool_call_outcomes.append({
                "tool": tool_name,
                "outcome": outcome,
                "matched_rule": last_rule,
            })

            _log(
                "GUARD",
                f"outcome={outcome} rule={last_rule} "
                f"dry_run={gr_resp.get('dry_run', False)}",
                indent=1,
            )
            if gr_resp.get("message"):
                _log("GUARD", gr_resp["message"], indent=1)

            # Branch on outcome
            tool_result_content = ""

            if outcome == "block":
                tool_result_content = (
                    f"Error: Action '{tool_name}' was BLOCKED by security policy. "
                    f"Rule: {last_rule}. Reason: {gr_resp.get('message', 'Policy violation')}. "
                    "The action was not executed. Please inform the user and suggest an alternative."
                )
                _log("EXEC", f"BLOCKED -- not executing {tool_name}", indent=1)

            elif outcome == "require_hitl":
                if dry_run:
                    tool_result_content = (
                        f"Action '{tool_name}' would require human approval (HITL) "
                        f"but this was a dry-run simulation. "
                        f"Rule: {last_rule}. Not executed."
                    )
                    _log("EXEC", f"DRY-RUN: would require HITL, skipping", indent=1)
                elif hitl_request_id:
                    _log(
                        "EXEC",
                        f"HITL pending (id={hitl_request_id}), auto-approving...",
                        indent=1,
                    )
                    approve_hitl(hitl_request_id, resolved_by="scenario-runner")
                    _log("EXEC", "HITL approved, executing simulated action...", indent=1)
                    result = execute_tool(tool_name, tool_input)
                    tool_result_content = result
                    _log("EXEC", f"Result: {result[:120]}...", indent=1)
                else:
                    tool_result_content = (
                        f"Action '{tool_name}' requires human approval (HITL). "
                        f"Pending request created. Awaiting resolution."
                    )
                    _log("EXEC", "HITL required, polling...", indent=1)
                    poll_result = poll_hitl(hitl_request_id)
                    if poll_result.get("status") == "timeout":
                        tool_result_content = (
                            f"Action '{tool_name}' HITL request timed out. "
                            "Action was NOT executed."
                        )
                    elif poll_result.get("status") == "approved":
                        result = execute_tool(tool_name, tool_input)
                        tool_result_content = result
                    else:
                        tool_result_content = (
                            f"Action '{tool_name}' was rejected by human review. "
                            "Not executed."
                        )

            else:
                # allow / log_and_allow
                result = execute_tool(tool_name, tool_input)
                tool_result_content = result
                _log("EXEC", f"Executed: {result[:120]}...", indent=1)

            # Send tool response back to Groq
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_result_content,
            })

    print()
    return {
        "prompt": user_prompt,
        "outcome": last_outcome,
        "matched_rule": last_rule,
        "final_text": final_text,
        "audit_written": audit_written,
        "tool_call_outcomes": tool_call_outcomes,
    }
