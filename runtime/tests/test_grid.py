"""Grid design space (probe positions), scan-report deliverables, and point matching."""

import csv
import json
import os
import subprocess
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest

from asb_lab.server import Lab, LabError, make_handler
from asb_verify.ledger_check import check_ledger
from asb_verify.points import match_points
from asb_verify.scan_report import load_scan_report

CLIENT = Path(__file__).resolve().parents[1] / "asb_client" / "asb.py"
ROWS, COLS = 4, 5


def value(row, col):
    return round(row * 10 + col + 0.25, 6)


def write_grid_campaign(root: Path, *, drop: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    table = {f"r{r}c{c}": {"signal": value(r, c)} for r in range(ROWS) for c in range(COLS)}
    table.pop(drop, None)
    (root / "acquisition.json").write_text(json.dumps({"format_version": 1, "values": table}))
    campaign = {
        "format_version": 1, "campaign_id": "test/grid", "source": "test",
        "objective": "Map the field of view.", "design_id_field": "position",
        "design_space": {"kind": "grid", "shape": [ROWS, COLS], "description": "probe positions"},
        "measurements": [{"name": "signal", "description": "", "higher_is_better": None}],
        "stages": [{"stage": n, "label": f"round {n}", "batch_size": 2} for n in (1, 2, 3)],
        "deliverable": {
            "kind": "scan-report", "format_version": 1, "filename": "scan_report.json",
            "max_bytes": 65536, "requires_stages": 1,
            "constraints": {"reconstruction_shape": [ROWS, COLS], "max_defects": 3},
        },
        "backend": {"kind": "replay", "table": "acquisition.json"},
    }
    path = root / "campaign.json"
    path.write_text(json.dumps(campaign))
    return path


def report(defects=(), shape=(ROWS, COLS)):
    return json.dumps({"format_version": 1,
                       "reconstruction": [[0.0] * shape[1] for _ in range(shape[0])],
                       "defects": [{"row": r, "col": c} for r, c in defects]})


@pytest.fixture
def lab(tmp_path):
    return Lab(write_grid_campaign(tmp_path / "campaign"), tmp_path / "state")


def test_card_exposes_grid_rules(lab):
    card = lab.campaign_card()
    assert card["design_space"]["kind"] == "grid" and card["design_space"]["shape"] == [ROWS, COLS]
    assert card["design_space"]["id_format"] == "r{row}c{col}"
    assert all("catalog_size" not in stage for stage in card["stages"])
    assert card["budget"]["total"] == 6


def test_orders_positions_and_returns_acquisition(lab):
    job = lab.submit_experiment({"designs": ["r0c0", "r3c4"]})["job"]
    assert job["results"] == [{"position": "r0c0", "signal": value(0, 0)},
                              {"position": "r3c4", "signal": value(3, 4)}]
    assert lab.status()["budget"]["spent"] == 2


@pytest.mark.parametrize("designs", [
    ["r4c0", "r0c0"],   # row out of bounds
    ["r0c5", "r0c0"],   # col out of bounds
    ["r01c2", "r0c0"],  # non-canonical id
    ["x", "r0c0"],      # not a position
    ["r1c1", "r1c1"],   # duplicate in batch
    ["r1c1"],           # wrong batch size
])
def test_invalid_positions_rejected_without_consuming(lab, designs):
    with pytest.raises(LabError) as error:
        lab.submit_experiment({"designs": designs})
    assert error.value.status == 400
    assert lab.status()["next_stage"] == 1 and lab.ledger.jobs == []


def test_position_measured_at_most_once(lab):
    lab.submit_experiment({"designs": ["r0c0", "r0c1"]})
    assert lab.submit_experiment({"designs": ["r0c0", "r0c1"]})["replayed"] is True
    with pytest.raises(LabError, match="already measured"):
        lab.submit_experiment({"designs": ["r0c1", "r2c2"]})
    assert lab.submit_experiment({"designs": ["r2c2", "r2c3"]})["job"]["stage"] == 2


def test_no_catalog(lab):
    with pytest.raises(LabError, match="no catalog") as error:
        lab.catalog(1)
    assert error.value.status == 400


def test_deliverable_after_one_stage(lab):
    with pytest.raises(LabError) as error:
        lab.submit_deliverable({"kind": "scan-report", "format_version": 1, "source": report()})
    assert error.value.status == 409
    lab.submit_experiment({"designs": ["r0c0", "r0c1"]})
    assert lab.submit_deliverable({"kind": "scan-report", "format_version": 1,
                                   "source": report()})["seq"] == 1


def test_replay_table_must_cover_grid(tmp_path):
    with pytest.raises(ValueError, match="r2c3"):
        Lab(write_grid_campaign(tmp_path / "c", drop="r2c3"), tmp_path / "state")


def test_ledger_check_allows_early_stop(lab, tmp_path):
    lab.submit_experiment({"designs": ["r0c0", "r1c1"]})
    lab.submit_experiment({"designs": ["r2c2", "r3c3"]})
    lab.submit_deliverable({"kind": "scan-report", "format_version": 1, "source": report()})
    ledger = json.loads((tmp_path / "state" / "ledger.json").read_text())
    pool = list(lab.campaign.grid_ids())
    truth = {f"r{r}c{c}": value(r, c) for r in range(ROWS) for c in range(COLS)}
    kwargs = dict(campaign_id="test/grid", backend="replay", stage_pools=[pool] * 3,
                  batch_sizes=[2] * 3, truth=truth, id_field="position", measurement="signal")
    assert check_ledger(ledger, min_stages=1, **kwargs) == ["r0c0", "r1c1", "r2c2", "r3c3"]
    with pytest.raises(AssertionError):
        check_ledger(ledger, **kwargs)                 # default: every stage required
    with pytest.raises(AssertionError):
        check_ledger(ledger, min_stages=3, **kwargs)


@pytest.fixture
def lab_url(tmp_path):
    lab = Lab(write_grid_campaign(tmp_path / "campaign"), tmp_path / "state")
    server = HTTPServer(("127.0.0.1", 0), make_handler(lab))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def asb(url, *args, check=True):
    return subprocess.run([sys.executable, str(CLIENT), *args], text=True, capture_output=True,
                          check=check, env={**os.environ, "ASB_LAB_URL": url})


def test_cli_grid_flow(lab_url, tmp_path):
    assert "4 x 5 grid" in asb(lab_url, "catalog", "--stage", "1").stdout
    (tmp_path / "b.csv").write_text("row,col\n3,4\n0,2\n")
    out = asb(lab_url, "run", str(tmp_path / "b.csv"), "-o", str(tmp_path / "r.csv")).stdout
    assert "stage 1 accepted: job-0001" in out
    rows = list(csv.DictReader((tmp_path / "r.csv").open()))
    assert rows == [{"row": "3", "col": "4", "signal": str(value(3, 4))},
                    {"row": "0", "col": "2", "signal": str(value(0, 2))}]
    (tmp_path / "bad.csv").write_text("position\nr0c0\n")
    assert "row,col" in asb(lab_url, "run", str(tmp_path / "bad.csv"), check=False).stderr
    (tmp_path / "again.csv").write_text("row,col\n0,2\n1,1\n")
    assert "already measured" in asb(lab_url, "run", str(tmp_path / "again.csv"), check=False).stderr


def test_cli_scan_report_validation(lab_url, tmp_path):
    good = tmp_path / "good.json"
    good.write_text(report(defects=[(1.5, 2.25)]))
    assert "local validation passed" in asb(lab_url, "deliver", str(good), "--validate-only").stdout
    for name, text in {
        "shape": report(shape=(ROWS, COLS + 1)),
        "bounds": report(defects=[(ROWS, 0)]),
        "count": report(defects=[(0, 0)] * 4),
        "nan": report().replace("0.0", "NaN", 1),
    }.items():
        bad = tmp_path / f"{name}.json"
        bad.write_text(text)
        assert "local validation failed" in asb(lab_url, "deliver", str(bad), check=False).stderr, name


def test_match_points():
    truth = [(10.0, 10.0), (20.0, 20.0), (30.0, 30.0)]
    assert match_points(truth, truth, 3)["f1"] == 1.0
    two_near_one = match_points([(10.5, 10.0), (11.0, 10.0)], truth, 3)
    assert (two_near_one["tp"], two_near_one["precision"]) == (1, 0.5)
    assert match_points([(13.0, 10.0)], truth, 3)["tp"] == 1      # radius is inclusive
    assert match_points([(13.1, 10.0)], truth, 3)["tp"] == 0
    assert match_points([], truth, 3) == {"tp": 0, "n_predicted": 0, "n_truth": 3,
                                          "precision": 0.0, "recall": 0.0, "f1": 0.0}
    # Greedy by distance: the closer prediction claims the shared truth point.
    contested = match_points([(22.0, 20.0), (20.5, 20.0)], [(20.0, 20.0)], 3)
    assert contested["tp"] == 1 and contested["precision"] == 0.5


def test_load_scan_report(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(report(defects=[(1, 2)]))
    loaded = load_scan_report(path, shape=(ROWS, COLS), max_defects=3)
    assert loaded["defects"] == [(1.0, 2.0)] and len(loaded["reconstruction"]) == ROWS
    for text in (report(shape=(ROWS + 1, COLS)), report(defects=[(0, COLS)]),
                 report(defects=[(0, 0)] * 4), json.dumps({"format_version": 1})):
        path.write_text(text)
        with pytest.raises(AssertionError):
            load_scan_report(path, shape=(ROWS, COLS), max_defects=3)
