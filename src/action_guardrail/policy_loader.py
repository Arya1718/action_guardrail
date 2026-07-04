from pathlib import Path

import yaml
from pydantic import ValidationError

from action_guardrail.models import Rule


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_policies(path: str) -> list[Rule]:
    policy_path = Path(path)
    if not policy_path.is_absolute():
        policy_path = (PROJECT_ROOT / policy_path).resolve()

    raw = policy_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)

    if not isinstance(data, dict) or "rules" not in data:
        raise ValueError("YAML root must be a dict with a 'rules' key")

    if not isinstance(data["rules"], list):
        raise ValueError("'rules' must be a list")

    rules: list[Rule] = []
    for i, entry in enumerate(data["rules"]):
        try:
            rule = Rule.model_validate(entry)
        except ValidationError as e:
            raise ValueError(f"Rule at index {i} is invalid: {e}") from e
        rules.append(rule)

    rules.sort(key=lambda r: r.priority)
    return rules
