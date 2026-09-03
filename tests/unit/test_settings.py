from pathlib import Path

from turbine_kg.settings import PROJECT_ROOT, Settings


def test_defaults_keep_source_and_database_outside_module_code(monkeypatch):
    for name in ("SOURCE_ROOT", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_environment(dotenv_path=PROJECT_ROOT / ".env.test-not-used")

    assert settings.source_root == (PROJECT_ROOT / "../Original materials").resolve()
    assert settings.neo4j_uri == "neo4j://localhost:7688"
    assert settings.neo4j_user == "neo4j"
    assert settings.neo4j_password is None


def test_relative_source_root_is_resolved_from_project_root(monkeypatch):
    monkeypatch.setenv("SOURCE_ROOT", "fixtures/source")

    settings = Settings.from_environment(dotenv_path=PROJECT_ROOT / ".env.test-not-used")

    assert settings.source_root == (PROJECT_ROOT / Path("fixtures/source")).resolve()


def test_dotenv_values_are_loaded_but_environment_values_win(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "SOURCE_ROOT=from-dotenv\nNEO4J_URI=neo4j://dotenv:7687\nNEO4J_PASSWORD='dotenv-secret'\n",
        encoding="utf-8",
    )
    for name in ("SOURCE_ROOT", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    loaded = Settings.from_environment(dotenv_path=dotenv)
    assert loaded.neo4j_uri == "neo4j://dotenv:7687"
    assert loaded.neo4j_password == "dotenv-secret"

    monkeypatch.setenv("NEO4J_URI", "neo4j://environment:7688")
    overridden = Settings.from_environment(dotenv_path=dotenv)
    assert overridden.neo4j_uri == "neo4j://environment:7688"
