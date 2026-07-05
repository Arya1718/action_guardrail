import json
import os
from pathlib import Path
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _ssm_get_parameter(name: str) -> str:
    """Read a parameter from SSM Parameter Store.
    Cached at module-load time (cold start), not per request.
    Raises RuntimeError on failure.
    """
    import boto3
    ssm = boto3.client("ssm")
    param = ssm.get_parameter(Name=name, WithDecryption=False)
    return param["Parameter"]["Value"]


def _resolve_from_ssm_or_env(name: str, ssm_path: str, env_var: str, default: str = "") -> str:
    """Resolve a config value from SSM (in Lambda) or env var (elsewhere)."""
    if "AWS_LAMBDA_FUNCTION_NAME" in os.environ:
        try:
            return _ssm_get_parameter(ssm_path)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to read {name} from SSM ({ssm_path}): {exc}"
            ) from exc
    return os.environ.get(env_var, default)


def _resolve_api_key() -> str:
    """Read API_KEY from SSM (/guardrail/api-key) when in Lambda,
    falling back to local env vars."""
    if "AWS_LAMBDA_FUNCTION_NAME" in os.environ:
        return _resolve_from_ssm_or_env("API_KEY", "/guardrail/api-key", "API_KEY", "dev-placeholder-key")
    return os.environ.get("API_KEY") or os.environ.get("GUARDRAIL_API_KEY") or "dev-placeholder-key"


def _resolve_mongo_uri() -> str:
    if "AWS_LAMBDA_FUNCTION_NAME" in os.environ:
        return _resolve_from_ssm_or_env("MONGO_URI", "/guardrail/mongo-uri", "MONGO_URI")
    return os.environ.get("MONGO_URI", "")


def _resolve_mongo_db_name() -> str:
    if "AWS_LAMBDA_FUNCTION_NAME" in os.environ:
        return _resolve_from_ssm_or_env("MONGO_DB_NAME", "/guardrail/mongo-db-name", "MONGO_DB_NAME", "guardrail_aws")
    return os.environ.get("MONGO_DB_NAME", "guardrail")


def _resolve_groq_api_key() -> str:
    if "AWS_LAMBDA_FUNCTION_NAME" in os.environ:
        try:
            return _ssm_get_parameter("/guardrail/groq-api-key")
        except Exception:
            return ""
    return os.environ.get("GROQ_API_KEY", "")


def resolve_org_id(api_key: str, settings: "Settings") -> Optional[str]:
    """Determine org_id from an API key.

    Returns None if the key is the admin key (access to all orgs),
    the org string for an org-specific key, or raises ValueError if
    the key is unknown.
    """
    if api_key == settings.API_KEY:
        return None
    try:
        mapping: dict[str, str] = json.loads(settings.ORG_API_KEYS) if settings.ORG_API_KEYS else {}
    except json.JSONDecodeError:
        mapping = {}
    for org_id, org_key in mapping.items():
        if org_key == api_key:
            return org_id
    raise ValueError("Invalid API key")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    POLICY_FILE_PATH: str = "policies/example_rules.yaml"
    STORAGE_BACKEND: str = "memory"
    API_KEY: str = ""

    MONGO_URI: str = ""
    MONGO_DB_NAME: str = "guardrail"
    SLACK_WEBHOOK_URL: str = ""
    ORG_API_KEYS: str = ""
    GROQ_API_KEY: str = ""

    @field_validator("API_KEY", mode="before")
    @classmethod
    def _resolve_api_key(cls, value: str) -> str:
        if value:
            return value
        return _resolve_api_key()

    @field_validator("MONGO_URI", mode="before")
    @classmethod
    def _resolve_mongo_uri(cls, value: str) -> str:
        if value:
            return value
        return _resolve_mongo_uri()

    @field_validator("MONGO_DB_NAME", mode="before")
    @classmethod
    def _resolve_mongo_db_name(cls, value: str) -> str:
        if value:
            return value
        return _resolve_mongo_db_name()

    @field_validator("GROQ_API_KEY", mode="before")
    @classmethod
    def _resolve_groq_api_key(cls, value: str) -> str:
        if value:
            return value
        return _resolve_groq_api_key()

    @field_validator("POLICY_FILE_PATH", mode="before")
    @classmethod
    def _resolve_policy_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute():
            return str(path)
        return str((PROJECT_ROOT / path).resolve())
