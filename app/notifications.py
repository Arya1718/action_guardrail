import logging
import os

import httpx

logger = logging.getLogger(__name__)


async def notify_slack(
    tool_name: str,
    outcome: str,
    matched_rule_id: str | None,
    reason: str,
    dry_run: bool = False,
) -> None:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        return
    if outcome not in ("block", "require_hitl"):
        return

    emoji = "\U0001F6AB" if outcome == "block" else "\U0001F440"
    rule_text = f"Matched rule: *{matched_rule_id}*" if matched_rule_id else "No rule matched"
    label = " [DRY RUN]" if dry_run else ""

    text = (
        f"{emoji} *Guardrail Alert*{label}\n"
        f"\u2022 Tool: `{tool_name}`\n"
        f"\u2022 Outcome: *{outcome}*\n"
        f"\u2022 {rule_text}\n"
        f"\u2022 Reason: {reason}"
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={"text": text})
            resp.raise_for_status()
        logger.info("Slack notification sent for %s (%s)", tool_name, outcome)
    except Exception as exc:
        logger.warning("Slack notification failed for %s (%s): %s", tool_name, outcome, exc)
