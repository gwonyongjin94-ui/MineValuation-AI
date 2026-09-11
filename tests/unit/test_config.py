from app.config import Settings


def test_settings_loads_env_file_regardless_of_cwd(tmp_path, monkeypatch):
    # Caught for real: launching uvicorn with --app-dir from outside the
    # project directory left Settings() unable to find SEC_USER_AGENT
    # because pydantic-settings resolved ".env" against the process's cwd,
    # not the project root - a 500 on every request in that setup.
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.sec_user_agent


def test_decision_log_path_resolves_against_the_project_root_not_the_cwd(tmp_path, monkeypatch):
    # Same failure the .env path above had: a relative default silently
    # becomes a different file for every working directory the app is
    # launched from, so a decision log would fragment across directories
    # instead of accumulating in one place.
    monkeypatch.chdir(tmp_path)

    resolved = Settings().resolved_decision_log_path

    assert resolved.is_absolute()
    assert tmp_path not in resolved.parents
    assert resolved.name == "decisions.jsonl"
    assert (resolved.parent.parent / "app" / "config.py").exists()


def test_an_absolute_decision_log_path_is_used_as_given(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "elsewhere.jsonl"))

    assert Settings().resolved_decision_log_path == tmp_path / "elsewhere.jsonl"
