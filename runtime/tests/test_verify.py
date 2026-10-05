import copy
import json
from pathlib import Path

import pytest

from conftest import VALUES
from asb_lab.server import Lab
from asb_verify.js_sandbox import run_model, verify_runner_isolation
from asb_verify.ledger_check import check_ledger
from asb_verify.ranking import ranking_metrics

RUNNER = Path(__file__).resolve().parents[1] / "sandbox" / "js_runner.ts"


def completed_ledger(campaign_path, tmp_path):
    lab = Lab(campaign_path, tmp_path / "state")
    lab.submit_experiment({"designs": ["d1", "d2"]})
    lab.submit_experiment({"designs": ["e1", "e3"]})
    lab.submit_deliverable({"kind": "js-predictor", "format_version": 1,
                            "source": "function predict(s){return 0;}"})
    return json.loads((tmp_path / "state" / "ledger.json").read_text())


def check(ledger):
    return check_ledger(
        ledger, campaign_id="test/mini", backend="replay",
        stage_pools=[["d1", "d2", "d3", "d4"], ["e1", "e2", "e3"]], batch_sizes=[2, 2],
        truth=VALUES, id_field="variant_id", measurement="activity",
    )


def test_ledger_accepted(campaign_path, tmp_path):
    assert check(completed_ledger(campaign_path, tmp_path)) == ["d1", "d2", "e1", "e3"]


@pytest.mark.parametrize("tamper", [
    lambda l: l["jobs"][0]["results"][0].update(activity=9.0),
    lambda l: l["jobs"].pop(),
    lambda l: l.update(backend="twin"),
    lambda l: l["jobs"][1].update(designs=["e1", "d3"]),
    lambda l: l.update(deliverables=[]),
])
def test_ledger_tampering_rejected(campaign_path, tmp_path, tamper):
    ledger = copy.deepcopy(completed_ledger(campaign_path, tmp_path))
    tamper(ledger)
    with pytest.raises(AssertionError):
        check(ledger)


def test_ranking_metrics():
    truth = [3.0, 2.0, 1.0, 0.0]
    perfect = ranking_metrics([4, 3, 2, 1], truth, threshold=1.0, k=2)
    assert perfect == {"ndcg": 1.0, "precision": 1.0}
    reversed_ = ranking_metrics([1, 2, 3, 4], truth, threshold=1.0, k=2)
    assert reversed_ == {"ndcg": 0.0, "precision": 0.5}
    tied = ranking_metrics([0, 0, 0, 0], truth, threshold=1.0, k=2)  # ties keep library order
    assert tied == perfect


def test_sandbox_runs_and_isolates():
    verify_runner_isolation(RUNNER)
    assert run_model("function predict(s){return s.length * 2;}", ["AB", "ABC"], RUNNER, 30) == [4.0, 6.0]
    with pytest.raises(AssertionError):
        run_model("function predict(s){return Date.now();}", ["A"], RUNNER, 30)


def test_ledger_progress(campaign_path, tmp_path):
    from asb_verify.ledger_check import ledger_progress
    lab = Lab(campaign_path, tmp_path / "state")
    lab.submit_experiment({"designs": ["d1", "d2"]})
    assert ledger_progress(tmp_path / "state" / "ledger.json") == {
        "stages_completed": 1, "designs_measured": 2, "deliverables_submitted": 0}
    assert ledger_progress(tmp_path / "missing.json")["ledger"] == "missing or unreadable"
