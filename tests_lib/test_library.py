"""Standalone tests for the action-guardrail library — no FastAPI/Mongo/Groq."""
from action_guardrail import (
    Condition,
    Decision,
    Match,
    Outcome,
    Rule,
    ToolCall,
    evaluate_action,
    load_policies,
)


def test_imports():
    assert evaluate_action is not None
    assert load_policies is not None
    assert Rule is not None
    assert ToolCall is not None
    assert Decision is not None


def test_block_outcome():
    rules = [
        Rule(
            id="block-big-delete",
            description="Block >100 record deletes",
            priority=10,
            action="block",
            match=Match(
                tool="delete_records",
                conditions=[Condition(field="count", operator="gt", value=100)],
            ),
        )
    ]
    decision = evaluate_action(
        ToolCall(tool="delete_records", parameters={"count": 500}), rules
    )
    assert decision.outcome == "block"
    assert decision.matched_rule_id == "block-big-delete"


def test_allow_default():
    decision = evaluate_action(
        ToolCall(tool="unknown_tool", parameters={}), []
    )
    assert decision.outcome == "allow"
    assert decision.matched_rule_id is None


def test_first_rule_wins():
    first = Rule(
        id="first",
        priority=10,
        action="block",
        match=Match(tool="test", conditions=[]),
    )
    second = Rule(
        id="second",
        priority=10,
        action="log_and_allow",
        match=Match(tool="test", conditions=[]),
    )
    decision = evaluate_action(ToolCall(tool="test", parameters={}), [first, second])
    assert decision.matched_rule_id == "first"


def test_condition_operators():
    rules = [
        Rule(
            id="ne-test",
            priority=10,
            action="block",
            match=Match(
                tool="test",
                conditions=[Condition(field="name", operator="ne", value="allowed")],
            ),
        )
    ]
    assert evaluate_action(
        ToolCall(tool="test", parameters={"name": "blocked"}), rules
    ).outcome == "block"
    assert evaluate_action(
        ToolCall(tool="test", parameters={"name": "allowed"}), rules
    ).outcome == "allow"


def test_version():
    import action_guardrail
    assert action_guardrail.__version__ == "0.1.0"
