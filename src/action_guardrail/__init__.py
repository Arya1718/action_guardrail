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

# Thin wrapper around the app.* modules.  When installed via pip install -e .
# (editable mode) the project root is on sys.path, so these imports resolve.
# For a formal PyPI release the three core modules would be vendored into this
# package — for now the existing app/ directory provides them.

from app.evaluator import evaluate_action  # noqa: F401
from app.models import (  # noqa: F401
    Condition,
    Decision,
    Match,
    Outcome,
    Rule,
    ToolCall,
)
from app.policy_loader import load_policies  # noqa: F401

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
