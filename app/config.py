from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchored to the project root, not left as the relative ".env" pydantic-
# settings resolves against the process's current working directory -
# that broke as soon as the app was launched with a different cwd (e.g.
# `uvicorn app.main:app --app-dir /path/to/project` from elsewhere), which
# is exactly how it's run under a process manager or container in practice.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


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
    # Where the decision log (app/memory/) is appended. Relative paths
    # resolve against the project root, not the process cwd, for the same
    # reason _ENV_FILE is anchored above. Deliberately a local file, not a
    # database: a deployed instance on an ephemeral filesystem (Render's
    # free plan, any container without a mounted volume) would lose it on
    # every restart, so the log is meant to accumulate on a machine that
    # actually keeps its disk - see docs/LIMITATIONS.md.
    decision_log_path: str = "data/decisions.jsonl"

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    @property
    def resolved_decision_log_path(self) -> Path:
        path = Path(self.decision_log_path)
        return path if path.is_absolute() else _PROJECT_ROOT / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
