from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchored to the project root, not left as the relative ".env" pydantic-
# settings resolves against the process's current working directory -
# that broke as soon as the app was launched with a different cwd (e.g.
# `uvicorn app.main:app --app-dir /path/to/project` from elsewhere), which
# is exactly how it's run under a process manager or container in practice.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    sec_user_agent: str
    # Optional so SEC-only functionality (and CI, until the secret is
    # configured there) doesn't break when no qualitative-analysis
    # feature actually needs it yet - checked at the point of use instead.
    anthropic_api_key: str | None = None
    # Comma-separated extra CORS origins for the GitHub Pages frontend
    # (docs/) - e.g. "https://<user>.github.io". Empty by default so a
    # bare API deployment (no frontend configured yet) doesn't need this
    # set; app/main.py always allows localhost regardless.
    allowed_origins: str = ""

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
