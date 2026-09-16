import hashlib

from turbine_kg.observability.lineage import verify_input_hashes


def test_verify_input_hashes_detects_stale_and_missing_inputs(tmp_path):
    path = tmp_path / "input.txt"
    path.write_text("before", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert verify_input_hashes(tmp_path, {"input": {"path": "input.txt", "sha256": digest}}) == []

    path.write_text("after", encoding="utf-8")
    failures = verify_input_hashes(tmp_path, {
        "stale": {"path": "input.txt", "sha256": digest},
        "missing": {"path": "missing.txt", "sha256": "0" * 64},
    })
    assert {item["reason"] for item in failures} == {"stale_artifact", "missing_artifact"}
