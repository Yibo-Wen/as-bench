import json

import pytest

from conftest import VALUES, write_campaign
from asb_lab.server import Lab, LabError

MODEL = {"kind": "js-predictor", "format_version": 1, "source": "function predict(s){return 1;}"}


def make_lab(campaign_path, tmp_path):
    return Lab(campaign_path, tmp_path / "state")


def test_initial_status(campaign_path, tmp_path):
    status = make_lab(campaign_path, tmp_path).status()
    assert status["stages_completed"] == 0
    assert status["next_stage"] == 1
    assert status["budget"] == {"unit": "measured designs", "total": 4, "spent": 0, "remaining": 4}
    assert status["deliverable_submitted"] is False


def test_public_card_hides_backend(campaign_path, tmp_path):
    card = make_lab(campaign_path, tmp_path).campaign_card()
    assert "backend" not in json.dumps(card)
    assert [s["catalog_size"] for s in card["stages"]] == [4, 3]


def test_stage_results_and_budget(campaign_path, tmp_path):
    lab = make_lab(campaign_path, tmp_path)
    response = lab.submit_experiment({"designs": ["d4", "d1"]})
    job = response["job"]
    assert (job["job_id"], job["stage"], job["status"]) == ("job-0001", 1, "completed")
    assert job["results"] == [
        {"variant_id": "d4", "activity": VALUES["d4"]},
        {"variant_id": "d1", "activity": VALUES["d1"]},
    ]
    status = lab.status()
    assert status["next_stage"] == 2
    assert status["budget"]["spent"] == 2


@pytest.mark.parametrize("payload, code", [
    ({"designs": ["d1"]}, 400),                      # wrong batch size
    ({"designs": ["d1", "d1"]}, 400),                # duplicate
    ({"designs": ["d1", "e1"]}, 400),                # not eligible in stage 1
    ({"designs": ["d1", "d2"], "extra": 1}, 400),    # unexpected field
    ({"designs": "d1,d2"}, 400),                     # wrong type
])
def test_rejection_does_not_advance(campaign_path, tmp_path, payload, code):
    lab = make_lab(campaign_path, tmp_path)
    before = (tmp_path / "state" / "ledger.json").read_text()
    with pytest.raises(LabError) as error:
        lab.submit_experiment(payload)
    assert error.value.status == code
    assert lab.status()["next_stage"] == 1
    assert (tmp_path / "state" / "ledger.json").read_text() == before


def test_repeating_latest_batch_replays(campaign_path, tmp_path):
    lab = make_lab(campaign_path, tmp_path)
    first = lab.submit_experiment({"designs": ["d1", "d2"]})
    again = lab.submit_experiment({"designs": ["d1", "d2"]})
    assert again["replayed"] is True
    assert again["job"] == first["job"]
    assert len(lab.ledger.jobs) == 1
    with pytest.raises(LabError):  # reordered batch is a new stage-2 request, ineligible
        lab.submit_experiment({"designs": ["d2", "d1"]})


def test_all_stages_then_closed(campaign_path, tmp_path):
    lab = make_lab(campaign_path, tmp_path)
    lab.submit_experiment({"designs": ["d1", "d2"]})
    lab.submit_experiment({"designs": ["e3", "e1"]})
    assert lab.status()["next_stage"] is None
    with pytest.raises(LabError) as error:
        lab.submit_experiment({"designs": ["e2", "e1"]})
    assert error.value.status == 409


def test_deliverable_gated_and_latest_wins(campaign_path, tmp_path):
    lab = make_lab(campaign_path, tmp_path)
    with pytest.raises(LabError) as error:
        lab.submit_deliverable(MODEL)
    assert error.value.status == 409
    lab.submit_experiment({"designs": ["d1", "d2"]})
    with pytest.raises(LabError):
        lab.submit_deliverable(MODEL)
    lab.submit_experiment({"designs": ["e1", "e2"]})
    with pytest.raises(LabError) as error:
        lab.submit_deliverable({**MODEL, "kind": "csv"})
    assert error.value.status == 400
    with pytest.raises(LabError):
        lab.submit_deliverable({**MODEL, "source": "x" * 2048})
    lab.submit_deliverable(MODEL)
    second = lab.submit_deliverable({**MODEL, "source": "function predict(s){return 2;}"})
    assert second["seq"] == 2
    stored = (tmp_path / "state" / "deliverables" / "final_model.js").read_text()
    assert stored.endswith("return 2;}")
    assert lab.status()["deliverable_submitted"] is True


def test_ledger_survives_restart(campaign_path, tmp_path):
    lab = make_lab(campaign_path, tmp_path)
    lab.submit_experiment({"designs": ["d1", "d2"]})
    restarted = make_lab(campaign_path, tmp_path)
    assert restarted.status()["next_stage"] == 2
    ledger = json.loads((tmp_path / "state" / "ledger.json").read_text())
    assert set(ledger) == {"format_version", "campaign_id", "backend", "jobs", "deliverables"}
    assert ledger["backend"] == "replay"


def test_unavailable_backend_does_not_consume_stage(tmp_path):
    path = write_campaign(tmp_path / "live", backend={"kind": "live"})
    lab = Lab(path, tmp_path / "state")
    with pytest.raises(LabError) as error:
        lab.submit_experiment({"designs": ["d1", "d2"]})
    assert error.value.status == 503
    assert lab.ledger.jobs == []
    assert lab.status()["next_stage"] == 1
