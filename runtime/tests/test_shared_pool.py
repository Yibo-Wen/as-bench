"""Shared-pool catalogs: every stage orders from one pool, each design at most once."""

import copy
import csv
import json
import os
import subprocess
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest

from conftest import POOL, write_shared_campaign
from asb_lab.backends.base import FAILED, JobUpdate
from asb_lab.server import Lab, LabError, make_handler
from asb_verify.ledger_check import check_ledger

CLIENT = Path(__file__).resolve().parents[1] / "asb_client" / "asb.py"
RUNNER = Path(__file__).resolve().parents[1] / "sandbox" / "js_runner.ts"
NAMES = ["yield", "n_replicates"]


@pytest.fixture
def lab(tmp_path):
    return Lab(write_shared_campaign(tmp_path / "campaign"), tmp_path / "state")


def test_card_states_shared_pool_rules(lab):
    card = lab.campaign_card()
    assert card["design_space"] == {
        "kind": "catalog", "shared_pool": True, "description": "alloy compositions",
        "rules": "every stage orders from the same catalog; each design can be measured at most once",
    }
    assert [stage["catalog_size"] for stage in card["stages"]] == [6, 6, 6]
    assert "backend" not in json.dumps(card)


def test_every_stage_offers_the_same_catalog(lab):
    assert lab.catalog(1)["designs"] == lab.catalog(3)["designs"]
    assert lab.catalog(1)["designs"][1] == {"composition": "Cu-0.98-In-0.02", "elements": "Cu|In"}


def test_design_measured_at_most_once(lab, tmp_path):
    job = lab.submit_experiment({"designs": ["Cu-1.00", "Cu-0.98-In-0.02"]})["job"]
    assert job["results"][1] == {"composition": "Cu-0.98-In-0.02", "yield": 3.0, "n_replicates": 2.0}
    assert lab.submit_experiment({"designs": ["Cu-1.00", "Cu-0.98-In-0.02"]})["replayed"] is True
    ledger = (tmp_path / "state" / "ledger.json").read_bytes()
    with pytest.raises(LabError, match="designs already measured") as error:
        lab.submit_experiment({"designs": ["Cu-0.98-In-0.02", "Cu-0.95-Pd-0.05"]})
    assert error.value.status == 400
    assert (tmp_path / "state" / "ledger.json").read_bytes() == ledger
    assert lab.status()["budget"]["spent"] == 2
    second = lab.submit_experiment({"designs": ["Cu-0.95-Pd-0.05", "Fe-0.50-Cu-0.50"]})["job"]
    assert second["stage"] == 2


def test_failed_job_frees_its_designs(lab, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(lab.backend, "submit", lambda *a, **k: JobUpdate(status=FAILED, error="x"))
        assert lab.submit_experiment({"designs": ["Cu-1.00", "Mn-0.02-Cu-0.98"]})["job"]["status"] == "failed"
    job = lab.submit_experiment({"designs": ["Mn-0.02-Cu-0.98", "Cu-1.00"]})["job"]
    assert job["stage"] == 1 and job["status"] == "completed"


@pytest.mark.parametrize("kwargs, message", [
    (dict(stage_catalogs=("pool.csv", "pool.csv", "short.csv")), "same catalog"),
    (dict(design_space={"kind": "grid", "shape": [2, 2], "shared_pool": True}), "only to catalog"),
    (dict(stage_catalogs=("pool.csv",) * 4), "exceeds the pool"),
])
def test_invalid_shared_pool_campaigns(tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        Lab(write_shared_campaign(tmp_path / "c", **kwargs), tmp_path / "s")


def test_overlap_without_shared_pool_still_rejected(tmp_path):
    path = write_shared_campaign(tmp_path / "c", design_space={"kind": "catalog"})
    with pytest.raises(ValueError, match="more than one stage"):
        Lab(path, tmp_path / "s")


def completed_ledger(lab, tmp_path):
    lab.submit_experiment({"designs": ["Cu-1.00", "Cu-0.98-In-0.02"]})
    lab.submit_experiment({"designs": ["Cu-0.95-Pd-0.05", "Fe-0.50-Cu-0.50"]})
    lab.submit_experiment({"designs": ["Mn-0.02-Cu-0.98", "Cu-0.96-Ag-0.03-In-0.01"]})
    lab.submit_deliverable({"kind": "js-predictor", "format_version": 1,
                            "source": "function predict(c){return 0;}"})
    return json.loads((tmp_path / "state" / "ledger.json").read_text())


def check(ledger, **overrides):
    kwargs = dict(campaign_id="test/shared", backend="replay", stage_pools=[list(POOL)] * 3,
                  batch_sizes=[2] * 3, truth=POOL, id_field="composition", measurements=NAMES)
    return check_ledger(ledger, **{**kwargs, **overrides})


def test_multi_measurement_ledger_check(lab, tmp_path):
    ledger = completed_ledger(lab, tmp_path)
    assert check(ledger) == list(POOL)
    with pytest.raises(ValueError, match="exactly one"):
        check(ledger, measurement="yield")
    with pytest.raises(ValueError, match="exactly one"):
        check(ledger, measurements=None)


@pytest.mark.parametrize("tamper", [
    lambda l: l["jobs"][0]["results"][0].update(n_replicates=1.0),   # second measurement
    lambda l: l["jobs"][0]["results"][0].pop("n_replicates"),          # missing key
    lambda l: l["jobs"][0]["results"][0].update(extra=1.0),            # extra key
    lambda l: l["jobs"][2].update(designs=["Cu-1.00", "Cu-0.96-Ag-0.03-In-0.01"]),  # repeat
])
def test_multi_measurement_tampering_rejected(lab, tmp_path, tamper):
    ledger = copy.deepcopy(completed_ledger(lab, tmp_path))
    tamper(ledger)
    with pytest.raises(AssertionError):
        check(ledger)


@pytest.fixture
def lab_url(lab):
    server = HTTPServer(("127.0.0.1", 0), make_handler(lab))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def asb(url, *args, check=True):
    env = {**os.environ, "ASB_LAB_URL": url, "ASB_JS_RUNNER": str(RUNNER)}
    return subprocess.run([sys.executable, str(CLIENT), *args], env=env, text=True,
                          capture_output=True, check=check)


def test_cli_shared_pool_flow(lab_url, tmp_path):
    out = asb(lab_url, "catalog", "--stage", "3", "-o", str(tmp_path / "pool.csv")).stdout
    assert "measured at most once" in out
    assert [r["composition"] for r in csv.DictReader((tmp_path / "pool.csv").open())] == list(POOL)
    (tmp_path / "b1.csv").write_text("composition\nCu-0.96-Ag-0.03-In-0.01\nCu-1.00\n")
    asb(lab_url, "run", str(tmp_path / "b1.csv"), "-o", str(tmp_path / "r1.csv"))
    assert (tmp_path / "r1.csv").read_text() == (
        "composition,yield,n_replicates\nCu-0.96-Ag-0.03-In-0.01,1.75,3.0\nCu-1.00,1.5,3.0\n")
    (tmp_path / "b2.csv").write_text("composition\nCu-1.00\nFe-0.50-Cu-0.50\n")
    assert "already measured" in asb(lab_url, "run", str(tmp_path / "b2.csv"), check=False).stderr
    model = tmp_path / "final_model.js"
    model.write_text("function predict(c){ return c.startsWith('Cu') ? 1 : c.length; }")
    assert "local validation passed" in asb(lab_url, "deliver", str(model), "--validate-only").stdout
