import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_api_key() -> str:
    """Read API_KEY from SSM Parameter Store when running in Lambda,
    falling back to the local env var.

    The SSM lookup is cached at module-load time (cold start) so it
    runs once per warm invocation lifespan, not per request.
    """
    if "AWS_LAMBDA_FUNCTION_NAME" in os.environ:
        try:
            import boto3
            ssm = boto3.client("ssm")
            param = ssm.get_parameter(
                Name="/guardrail/api-key",
                WithDecryption=False,
            )
            return param["Parameter"]["Value"]
        except Exception as exc:
            raise RuntimeError(
                f"Failed to read API key from SSM: {exc}"
            ) from exc
    return os.environ.get("API_KEY", "dev-placeholder-key")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    POLICY_FILE_PATH: str = "policies/example_rules.yaml"
    STORAGE_BACKEND: str = "memory"
    API_KEY: str = ""

    MONGO_URI: str = ""
    MONGO_DB_NAME: str = "guardrail"

    @field_validator("API_KEY", mode="before")
    @classmethod
    def _resolve_api_key(cls, value: str) -> str:
        if value:
            return value
        return _resolve_api_key()

    @field_validator("POLICY_FILE_PATH", mode="before")
    @classmethod
    def _resolve_policy_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute():
            return str(path)
        return str((PROJECT_ROOT / path).resolve())
