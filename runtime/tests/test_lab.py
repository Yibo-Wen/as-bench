import csv
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


def test_live_inbox_protocol(tmp_path):
    """A laboratory that answers with files: queued, then running, then completed."""
    inbox = tmp_path / "inbox"
    path = write_campaign(tmp_path / "live-inbox",
                          backend={"kind": "live", "inbox": str(inbox), "label": "plate"})
    lab = Lab(path, tmp_path / "state")
    job = lab.submit_experiment({"designs": ["d1", "d4"]})["job"]
    assert job["status"] == "queued"
    assert lab.status()["budget"]["spent"] == 2          # an accepted job holds its designs

    # The lab wrote the batch out for a person to pick up, in the ordered form.
    directory = inbox / job["job_id"]
    assert [row.strip() for row in (directory / "batch.csv").read_text().splitlines()] == [
        "variant_id", "d1", "d4"]
    request = json.loads((directory / "request.json").read_text())
    assert request["n_designs"] == 2 and request["order_is_the_ranking"] is True

    (directory / "running").write_text("on the instrument\n")
    assert lab.get_job(job["job_id"])["job"]["status"] == "running"

    # Results come back out of order; the backend returns them in batch order.
    (directory / "results.csv").write_text("variant_id,activity\nd4,0.75\nd1,0.25\n")
    done = lab.get_job(job["job_id"])["job"]
    assert done["status"] == "completed"
    assert done["results"] == [{"variant_id": "d1", "activity": 0.25},
                               {"variant_id": "d4", "activity": 0.75}]


def test_live_inbox_incomplete_results_fail_the_job(tmp_path):
    """A hole in the results file fails the job instead of completing it with a gap."""
    inbox = tmp_path / "inbox"
    path = write_campaign(tmp_path / "live-partial",
                          backend={"kind": "live", "inbox": str(inbox)})
    lab = Lab(path, tmp_path / "state")
    job = lab.submit_experiment({"designs": ["d1", "d4"]})["job"]
    (inbox / job["job_id"] / "results.csv").write_text("variant_id,activity\nd1,0.25\n")
    failed = lab.get_job(job["job_id"])["job"]
    assert failed["status"] == "failed"
    assert "missing 1 designs" in failed["error"]
    # A failed job does not consume its stage, so the batch can be reordered.
    assert lab.status()["next_stage"] == 1
    assert lab.submit_experiment({"designs": ["d1", "d4"]})["job"]["status"] == "queued"


def test_live_inbox_laboratory_can_fail_a_job(tmp_path):
    inbox = tmp_path / "inbox"
    path = write_campaign(tmp_path / "live-failed",
                          backend={"kind": "live", "inbox": str(inbox)})
    lab = Lab(path, tmp_path / "state")
    job = lab.submit_experiment({"designs": ["d1", "d4"]})["job"]
    (inbox / job["job_id"] / "failed").write_text("reagent lot expired\nsecond line\n")
    failed = lab.get_job(job["job_id"])["job"]
    assert failed["status"] == "failed" and failed["error"] == "reagent lot expired"


def _mixed_campaign(root, batch, counts, rows=6):
    """A one-stage catalog campaign whose designs come in two kinds."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "catalog").mkdir(exist_ok=True)
    with (root / "catalog" / "stage1.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["variant_id", "kind"])
        writer.writerows([[f"a{i}", "one"] for i in range(1, rows + 1)]
                         + [[f"b{i}", "two"] for i in range(1, rows + 1)])
    stage = {"stage": 1, "label": "mixed", "batch_size": batch,
             "catalog": "catalog/stage1.csv"}
    if counts is not None:
        stage["composition"] = {"column": "kind", "counts": counts}
    campaign = {
        "format_version": 1, "campaign_id": "test/mixed", "source": "test",
        "objective": "Find active designs.", "design_id_field": "variant_id",
        "design_space": {"kind": "catalog", "shared_pool": False, "description": ""},
        "measurements": [{"name": "activity", "description": "",
                          "higher_is_better": True}],
        "stages": [stage],
        "deliverable": {"kind": "design-submission", "format_version": 1,
                        "filename": "submission.csv", "max_bytes": 1024,
                        "content": "text", "description": "", "requires_stages": 0},
        "backend": {"kind": "live", "inbox": str(root / "inbox"), "label": "plate"},
    }
    path = root / "campaign.json"
    path.write_text(json.dumps(campaign))
    return path


def test_stage_composition_rejects_the_wrong_mix(tmp_path):
    """A composition rule fixes the batch's mix, with 400 and no stage consumed.

    The campaign this exists for offers one- and two-substitution designs whose base
    rates differ about two-fold, so a submission that chose its own mix would be scored
    on denominators it picked. Fixing the mix keeps submissions on the same footing, and
    rejecting at order time means a wrong mix costs a retry rather than the round.
    """
    path = _mixed_campaign(tmp_path / "mixed", batch=4, counts={"one": 2, "two": 2})
    lab = Lab(path, tmp_path / "state")
    assert lab.campaign.public_card()["stages"][0]["composition"] == {
        "column": "kind", "counts": {"one": 2, "two": 2}}
    with pytest.raises(LabError) as wrong:
        lab.submit_experiment({"designs": ["a1", "a2", "a3", "b1"]})
    assert wrong.value.status == 400
    assert "composition" in str(wrong.value)
    assert lab.status()["next_stage"] == 1, "a rejected mix must not consume the stage"
    job = lab.submit_experiment({"designs": ["a1", "b1", "a2", "b2"]})["job"]
    assert job["status"] == "queued"


def test_composition_counts_must_sum_to_the_batch(tmp_path):
    """A rule that cannot be satisfied is a campaign error, not a runtime surprise."""
    path = _mixed_campaign(tmp_path / "bad", batch=2, counts={"one": 2, "two": 2})
    with pytest.raises(ValueError, match="sum to batch_size"):
        Lab(path, tmp_path / "state")
