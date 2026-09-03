from pathlib import Path

from turbine_kg.settings import PROJECT_ROOT, Settings


def test_defaults_keep_source_and_database_outside_module_code(monkeypatch):
    for name in ("SOURCE_ROOT", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_environment()

    assert settings.source_root == (PROJECT_ROOT / "../Original materials").resolve()
    assert settings.neo4j_uri == "neo4j://localhost:7688"
    assert settings.neo4j_user == "neo4j"
    assert settings.neo4j_password is None


def test_relative_source_root_is_resolved_from_project_root(monkeypatch):
    monkeypatch.setenv("SOURCE_ROOT", "fixtures/source")

    settings = Settings.from_environment()

    assert settings.source_root == (PROJECT_ROOT / Path("fixtures/source")).resolve()
