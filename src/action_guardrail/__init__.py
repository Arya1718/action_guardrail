"""action-guardrail — standalone policy evaluation library.

Usage:
    from action_guardrail import load_policies, evaluate_action, ToolCall

    rules = load_policies("policies.yaml")
    decision = evaluate_action(
        ToolCall(tool="delete_records", parameters={"record_count": 500}),
        rules,
    )
    print(decision.outcome)  # "block"
"""

from action_guardrail.evaluator import evaluate_action  # noqa: F401
from action_guardrail.models import (  # noqa: F401
    Condition,
    Decision,
    Match,
    Outcome,
    Rule,
    ToolCall,
)
from action_guardrail.policy_loader import load_policies  # noqa: F401

__all__ = [
    "evaluate_action",
    "load_policies",
    "Condition",
    "Decision",
    "Match",
    "Outcome",
    "Rule",
    "ToolCall",
]
__version__ = "0.1.0"
