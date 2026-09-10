from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _assert_job_env_contexts_are_valid(path: Path) -> None:
    doc = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(doc, dict)
    for job_name, job in doc.get("jobs", {}).items():
        for variable, value in job.get("env", {}).items():
            assert "${{ runner." not in str(value), (
                f"{path}: job-level env {job_name}.{variable} uses the runner "
                "context, which GitHub does not make available there"
            )


def test_active_paper_workflow_contexts() -> None:
    _assert_job_env_contexts_are_valid(ROOT / ".github/workflows/trend3-paper.yml")


def test_standalone_trade_workflow_contexts() -> None:
    _assert_job_env_contexts_are_valid(
        ROOT / "nextgen/.github/workflows/trade.yml"
    )


def test_active_paper_workflow_covers_both_new_york_utc_offsets() -> None:
    path = ROOT / ".github/workflows/trend3-paper.yml"
    doc = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    schedules = {entry["cron"] for entry in doc["on"]["schedule"]}
    assert schedules == {
        "35,50 13 * * *",
        "5,35,50 14 * * *",
        "5 15 * * *",
    }
