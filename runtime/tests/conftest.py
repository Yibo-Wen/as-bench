import csv
import json
import os
import sys
from pathlib import Path

import pytest

# Verifier helpers read ASB_VERIFY_LOCAL at import time.
os.environ["ASB_VERIFY_LOCAL"] = "1"

RUNTIME = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(RUNTIME), str(RUNTIME / "asb_client")]

VALUES = {
    "d1": 0.5, "d2": 0.25, "d3": -0.125, "d4": 1.0,
    "e1": 0.75, "e2": 0.0, "e3": 2.0,
}


def write_campaign(root: Path, backend: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    stages = {1: ["d1", "d2", "d3", "d4"], 2: ["e1", "e2", "e3"]}
    for number, ids in stages.items():
        with (root / f"stage_{number}.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["variant_id", "sequence"])
            writer.writerows([[i, f"SEQ{i.upper()}"] for i in ids])
    (root / "measurements.json").write_text(json.dumps({
        "format_version": 1,
        "values": {k: {"activity": v} for k, v in VALUES.items()},
    }))
    campaign = {
        "format_version": 1,
        "campaign_id": "test/mini",
        "source": "test",
        "objective": "Find active designs.",
        "design_id_field": "variant_id",
        "measurements": [{"name": "activity", "description": "", "higher_is_better": True}],
        "stages": [
            {"stage": 1, "label": "singles", "batch_size": 2, "catalog": "stage_1.csv"},
            {"stage": 2, "label": "doubles", "batch_size": 2, "catalog": "stage_2.csv"},
        ],
        "deliverable": {
            "kind": "js-predictor", "format_version": 1, "filename": "final_model.js",
            "max_bytes": 1024, "input_field": "sequence", "description": "",
        },
        "backend": backend or {"kind": "replay", "table": "measurements.json"},
    }
    path = root / "campaign.json"
    path.write_text(json.dumps(campaign))
    return path


@pytest.fixture
def campaign_path(tmp_path: Path) -> Path:
    return write_campaign(tmp_path / "campaign")


POOL = {
    "Cu-1.00": {"yield": 1.5, "n_replicates": 3},
    "Cu-0.98-In-0.02": {"yield": 3.0, "n_replicates": 2},
    "Cu-0.95-Pd-0.05": {"yield": 2.25, "n_replicates": 3},
    "Fe-0.50-Cu-0.50": {"yield": 0.125, "n_replicates": 1},
    "Mn-0.02-Cu-0.98": {"yield": 0.0, "n_replicates": 3},
    "Cu-0.96-Ag-0.03-In-0.01": {"yield": 1.75, "n_replicates": 3},
}


def write_shared_campaign(root: Path, *, stage_catalogs=("pool.csv",) * 3,
                          design_space: dict | None = None) -> Path:
    """Three stages ordering from one pool; composition is both ID and model input."""
    root.mkdir(parents=True, exist_ok=True)
    for name in set(stage_catalogs):
        ids = list(POOL) if name == "pool.csv" else list(POOL)[:-1]
        with (root / name).open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["composition", "elements"])
            writer.writerows([[i, "|".join(i.split("-")[::2])] for i in ids])
    (root / "measurements.json").write_text(json.dumps({"format_version": 1, "values": POOL}))
    campaign = {
        "format_version": 1,
        "campaign_id": "test/shared",
        "source": "test",
        "objective": "Find active compositions.",
        "design_id_field": "composition",
        "design_space": design_space or {"kind": "catalog", "shared_pool": True,
                                         "description": "alloy compositions"},
        "measurements": [
            {"name": "yield", "description": "", "higher_is_better": True},
            {"name": "n_replicates", "description": "", "higher_is_better": None},
        ],
        "stages": [{"stage": n, "label": f"round {n}", "batch_size": 2, "catalog": name}
                   for n, name in enumerate(stage_catalogs, start=1)],
        "deliverable": {
            "kind": "js-predictor", "format_version": 1, "filename": "final_model.js",
            "max_bytes": 1024, "input_field": "composition", "description": "",
        },
        "backend": {"kind": "replay", "table": "measurements.json"},
    }
    path = root / "campaign.json"
    path.write_text(json.dumps(campaign))
    return path
