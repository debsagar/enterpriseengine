"""Offline tests for end-of-run verification and observable/private run layout."""

import json

from environments.norvale.verify_layout import verify_run, write_run_layout


def test_verify_run_and_layout_pass(tmp_path):
    (tmp_path / "lake" / "manifests").mkdir(parents=True)
    (tmp_path / "lake" / "delta").mkdir(parents=True)
    (tmp_path / "business").mkdir(parents=True)
    (tmp_path / "business" / "trace.jsonl").write_text('{"kind":"test"}\n')
    (tmp_path / "business" / "behaviour.json").write_text("{}\n")
    (tmp_path / "provision.log").write_text("ok\n")
    (tmp_path / "lake" / "manifests" / "2027-01-01.json").write_text(
        json.dumps({"day": "2027-01-01", "failed_sources": [], "counts": {"erp/Item": 1}})
    )

    cfg = {"version": 1, "days": 1, "seed": 1, "opening_orders": []}
    write_run_layout(tmp_path, cfg, revision="abc1234", started_at="2026-11-26T00:00:00Z")
    report = verify_run(tmp_path, cfg)

    assert report["ok"] is True
    assert report["manifests_found"] == 1
    assert (tmp_path / "run.json").is_file()
    assert (tmp_path / "verification.json").is_file()
    assert (tmp_path / "observable" / "days" / "2027-01-01" / "manifest.json").is_file()
    assert (tmp_path / "private" / "trace" / "trace.jsonl").is_file()


def test_verify_run_fails_on_missing_day(tmp_path):
    (tmp_path / "lake" / "manifests").mkdir(parents=True)
    cfg = {"version": 1, "days": 2, "seed": 1, "opening_orders": []}
    (tmp_path / "lake" / "manifests" / "2027-01-01.json").write_text(
        json.dumps({"day": "2027-01-01", "failed_sources": []})
    )
    report = verify_run(tmp_path, cfg)
    assert report["ok"] is False
    assert report["missing_days"] == ["2027-01-02"]
