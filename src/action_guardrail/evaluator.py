import logging
import re
from datetime import datetime, timezone
from typing import Any

from action_guardrail.models import Condition, Decision, Outcome, Rule, ToolCall

logger = logging.getLogger(__name__)


def _evaluate_condition(condition: Condition, parameters: dict[str, Any]) -> bool:
    if condition.field not in parameters:
        logger.warning(
            "Condition field '%s' not found in parameters; treating as no match",
            condition.field,
        )
        return False

    param_value = parameters[condition.field]

    try:
        return _apply_operator(condition.operator, param_value, condition.value)
    except Exception as exc:
        logger.error(
            "Error evaluating condition (field=%s, op=%s): %s",
            condition.field,
            condition.operator,
            exc,
        )
        return False


def _apply_operator(op: str, param_value: Any, target_value: Any) -> bool:
    if op == "eq":
        return param_value == target_value
    elif op == "ne":
        return param_value != target_value
    elif op == "gt":
        return _cmp(param_value, target_value, "gt")
    elif op == "gte":
        return _cmp(param_value, target_value, "gte")
    elif op == "lt":
        return _cmp(param_value, target_value, "lt")
    elif op == "lte":
        return _cmp(param_value, target_value, "lte")
    elif op == "in":
        if not isinstance(target_value, (list, tuple, set)):
            raise TypeError(f"'in' operator requires a list/tuple/set, got {type(target_value).__name__}")
        return param_value in target_value
    elif op == "not_in":
        if not isinstance(target_value, (list, tuple, set)):
            raise TypeError(f"'not_in' operator requires a list/tuple/set, got {type(target_value).__name__}")
        return param_value not in target_value
    elif op == "contains":
        if not isinstance(param_value, str):
            raise TypeError(f"'contains' operator requires a string parameter, got {type(param_value).__name__}")
        return target_value in param_value
    elif op == "regex":
        if not isinstance(param_value, str):
            raise TypeError(f"'regex' operator requires a string parameter, got {type(param_value).__name__}")
        return bool(re.search(str(target_value), param_value))
    else:
        raise ValueError(f"Unknown operator: {op}")


def _cmp(a: Any, b: Any, op: str) -> bool:
    try:
        if op == "gt":
            return bool(a > b)
        elif op == "gte":
            return bool(a >= b)
        elif op == "lt":
            return bool(a < b)
        elif op == "lte":
            return bool(a <= b)
    except TypeError as e:
        raise TypeError(
            f"Cannot compare {type(a).__name__} with {type(b).__name__} using '{op}': {e}"
        ) from e
    return False


def evaluate_action(tool_call: ToolCall, rules: list[Rule]) -> Decision:
    for rule in rules:
        if tool_call.tool != rule.match.tool:
            continue

        all_match = True
        for condition in rule.match.conditions:
            if not _evaluate_condition(condition, tool_call.parameters):
                all_match = False
                break

        if all_match:
            return Decision(
                outcome=rule.action,
                matched_rule_id=rule.id,
                reason=f"Matched rule '{rule.id}': {rule.description}",
                evaluated_at=datetime.now(timezone.utc),
            )

    return Decision(
        outcome="allow",
        matched_rule_id=None,
        reason="No matching rule",
        evaluated_at=datetime.now(timezone.utc),
    )
