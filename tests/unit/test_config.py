from app.config import Settings


def test_settings_loads_env_file_regardless_of_cwd(tmp_path, monkeypatch):
    # Caught for real: launching uvicorn with --app-dir from outside the
    # project directory left Settings() unable to find SEC_USER_AGENT
    # because pydantic-settings resolved ".env" against the process's cwd,
    # not the project root - a 500 on every request in that setup.
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.sec_user_agent
