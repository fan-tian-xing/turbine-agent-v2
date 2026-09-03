from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_and_secret_paths_are_ignored():
    ignore_rules = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    for rule in (".env", "/var/", "/docker-data/", "/data/staging/"):
        assert rule in ignore_rules


def test_compose_uses_isolated_names_ports_and_mounts():
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")

    for expected in (
        "name: turbine-neo4j-v2",
        "image: neo4j:5.26.29",
        '"7475:7474"',
        '"7688:7687"',
        "./docker-data/neo4j-data:/data",
        "./docker-data/neo4j-logs:/logs",
    ):
        assert expected in compose
