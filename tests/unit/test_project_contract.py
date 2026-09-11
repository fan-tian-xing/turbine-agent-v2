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
        "healthcheck:",
        "cypher-shell",
    ):
        assert expected in compose


def test_ocr_output_path_is_bound_to_configured_derivative_root(tmp_path: Path):
    from scripts.generate_ocr_pdf import validate_output_path

    root = tmp_path / "ocr"
    output = validate_output_path(root / "derived.pdf", root)

    assert output == (root / "derived.pdf").resolve()


def test_ocr_output_path_rejects_source_or_arbitrary_locations(tmp_path: Path):
    from scripts.generate_ocr_pdf import validate_output_path

    root = tmp_path / "ocr"
    for output in (tmp_path / "source.pdf", tmp_path / "elsewhere" / "derived.pdf"):
        try:
            validate_output_path(output, root)
        except ValueError as exc:
            assert "OCR_DERIVED_ROOT" in str(exc)
        else:
            raise AssertionError("OCR output escaped the configured derivative root")


def test_ocr_font_path_is_explicitly_configured(monkeypatch, tmp_path: Path):
    from scripts.generate_ocr_pdf import default_font_file

    monkeypatch.delenv("OCR_FONT_FILE", raising=False)
    assert default_font_file() is None

    font = tmp_path / "font.ttf"
    font.write_bytes(b"font")
    monkeypatch.setenv("OCR_FONT_FILE", str(font))
    assert default_font_file() == font.resolve()


def test_roadmap_orders_claim_validation_before_cli_and_activation():
    plan = (PROJECT_ROOT / "总计划.md").read_text(encoding="utf-8")

    claim_stage = plan.index("### 阶段 18：逐结论校验")
    cli_stage = plan.index("### 阶段 19：建设 CLI 问答")
    evaluation_stage = plan.index("### 阶段 20：分层测试和未知盲测")

    assert claim_stage < cli_stage < evaluation_stage
    assert "阶段 17 不写入 active Release" in plan
    assert "才允许在维护窗口把经过测试的同一 candidate 原样晋级为 `active_release`" in plan
    assert "本阶段范围明确为 `golden_sample_only`" in plan
    assert "`terminology_input_manifest`" in plan
