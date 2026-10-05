"""Upload design spaces, file measurements, the twin backend, and binary deliverables."""

import base64
import io
import json
import os
import subprocess
import sys
import threading
import time
import zlib
from http.server import HTTPServer
from pathlib import Path

import numpy as np
import pytest

from asb_lab.server import Lab, LabError, make_handler
from asb_verify.ledger_check import check_ledger_structure, ledger_progress
from asb_verify.report import evaluate_gates

CLIENT = Path(__file__).resolve().parents[1] / "asb_client" / "asb.py"

TWIN = '''
import json, time
from pathlib import Path

def validate_upload(stage, upload):
    if not upload.data.startswith(b"\\x93NUMPY"):
        raise ValueError("not a .npy file")

def validate_deliverable(data):
    if not data.startswith(b"\\x93NUMPY"):
        raise ValueError("not a .npy file")

def measure(stage, designs, output_dir, uploads):
    rows = []
    for index, upload in enumerate(uploads):
        if upload.name == "explode.npy":
            raise RuntimeError("instrument fault")
        time.sleep(0.05)
        name = upload.name.removesuffix(".npy") + "_sem.npy"
        (Path(output_dir) / name).write_bytes(upload.data[::-1])
        rows.append({"design": upload.name, "sem": name, "size": len(upload.data)})
    return rows
'''


def npy(array) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, np.asarray(array))
    return buffer.getvalue()


def blob(raw: bytes, compress: bool = True) -> dict:
    data = zlib.compress(raw) if compress else raw
    return {"encoding": "zlib+base64" if compress else "base64",
            "data": base64.b64encode(data).decode()}


@pytest.fixture
def campaign_path(tmp_path):
    root = tmp_path / "campaign"
    (root / "twin").mkdir(parents=True)
    (root / "twin" / "process.py").write_text(TWIN)
    (root / "campaign.json").write_text(json.dumps({
        "format_version": 1,
        "campaign_id": "test/upload",
        "source": "test",
        "objective": "Print masks.",
        "design_id_field": "design",
        "design_space": {"kind": "upload", "format": "npy-binary-mask", "max_bytes": 4096,
                         "constraints": {"shape": [4, 4]}},
        "measurements": [
            {"name": "sem", "kind": "file", "description": "SEM image"},
            {"name": "size", "description": "bytes", "higher_is_better": None},
        ],
        "stages": [{"stage": i, "label": f"round {i}", "batch_size": 2} for i in (1, 2, 3)],
        "deliverable": {"kind": "npy-binary-mask", "format_version": 1, "content": "binary",
                        "filename": "target_mask.npy", "max_bytes": 4096, "requires_stages": 1,
                        "constraints": {"shape": [4, 4]}},
        "backend": {"kind": "twin", "module": "twin/process.py"},
    }))
    return root / "campaign.json"


def designs(*names, content=None):
    return [{"name": n, **blob(content or npy(np.eye(4, dtype=np.uint8)))} for n in names]


def wait(lab, job_id):
    for _ in range(200):
        job = lab.get_job(job_id)["job"]
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_upload_job_runs_async_and_serves_files(campaign_path, tmp_path):
    lab = Lab(campaign_path, tmp_path / "state")
    response = lab.submit_experiment({"designs": designs("a.npy", "b.npy")})
    job = response["job"]
    assert job["status"] == "running" and job["stage"] == 1
    assert job["designs"][0].startswith("a.npy@sha256:")
    assert lab.status()["active_job"] == job["job_id"]
    with pytest.raises(LabError) as error:  # one job in flight at a time
        lab.submit_experiment({"designs": designs("c.npy", "d.npy")})
    assert error.value.status == 409
    done = wait(lab, job["job_id"])
    assert [r["sem"] for r in done["results"]] == ["a_sem.npy", "b_sem.npy"]
    assert lab.job_file(job["job_id"], "a_sem.npy") == npy(np.eye(4, dtype=np.uint8))[::-1]
    with pytest.raises(LabError):
        lab.job_file(job["job_id"], "../ledger.json")
    again = lab.submit_experiment({"designs": designs("a.npy", "b.npy")})
    assert again["replayed"] is True and again["job"]["job_id"] == job["job_id"]
    assert lab.status()["budget"] == {"unit": "measured designs", "total": 6, "spent": 2, "remaining": 4}


@pytest.mark.parametrize("bad", [
    designs("a.npy"),                                                  # wrong batch size
    designs("a.npy", "a.npy"),                                         # duplicate name
    designs("../x.npy", "b.npy"),                                      # unsafe name
    designs("a.npy", "b.npy", content=b"not numpy"),                  # twin validator
    [{"name": "a.npy", "encoding": "rot13", "data": "x"}] * 2,         # unknown encoding
    [{"name": n, **blob(b"\x93NUMPY" + b"0" * 10_000)} for n in ("a.npy", "b.npy")],  # too big
])
def test_rejected_uploads_do_not_consume_a_stage(campaign_path, tmp_path, bad):
    lab = Lab(campaign_path, tmp_path / "state")
    with pytest.raises(LabError) as error:
        lab.submit_experiment({"designs": bad})
    assert error.value.status == 400
    assert lab.ledger.jobs == [] and lab.status()["next_stage"] == 1


def test_measurement_failure_frees_the_stage(campaign_path, tmp_path):
    lab = Lab(campaign_path, tmp_path / "state")
    job = lab.submit_experiment({"designs": designs("explode.npy", "b.npy")})["job"]
    assert wait(lab, job["job_id"])["status"] == "failed"
    assert lab.status()["next_stage"] == 1 and lab.status()["budget"]["spent"] == 0
    retry = lab.submit_experiment({"designs": designs("a.npy", "b.npy")})["job"]
    assert retry["stage"] == 1 and wait(lab, retry["job_id"])["status"] == "completed"


def test_binary_deliverable_after_required_stages(campaign_path, tmp_path):
    lab = Lab(campaign_path, tmp_path / "state")
    mask = {"kind": "npy-binary-mask", "format_version": 1, **blob(npy(np.ones((4, 4), np.uint8)))}
    with pytest.raises(LabError) as error:
        lab.submit_deliverable(mask)
    assert error.value.status == 409 and "at least 1" in str(error.value)
    wait(lab, lab.submit_experiment({"designs": designs("a.npy", "b.npy")})["job"]["job_id"])
    with pytest.raises(LabError) as error:
        lab.submit_deliverable({**mask, **blob(b"garbage")})
    assert error.value.status == 400
    assert lab.submit_deliverable(mask)["seq"] == 1
    stored = tmp_path / "state" / "deliverables" / "target_mask.npy"
    assert np.array_equal(np.load(stored), np.ones((4, 4)))
    card = lab.campaign_card()
    assert card["design_space"]["kind"] == "upload"
    assert card["deliverable"]["requires"] == "at least 1 completed stage(s)"
    assert "backend" not in json.dumps(card)

    ledger = json.loads((tmp_path / "state" / "ledger.json").read_text())
    assert check_ledger_structure(ledger, campaign_id="test/upload", backend="twin",
                                  batch_sizes=[2, 2, 2], min_stages=1) == 1
    with pytest.raises(AssertionError):
        check_ledger_structure(ledger, campaign_id="test/upload", backend="twin",
                               batch_sizes=[2, 2, 2], min_stages=2)
    assert ledger_progress(tmp_path / "state" / "ledger.json") == {
        "stages_completed": 1, "designs_measured": 2, "deliverables_submitted": 1}


def test_zlib_bomb_rejected(campaign_path, tmp_path):
    lab = Lab(campaign_path, tmp_path / "state")
    bomb = [{"name": n, **blob(b"\x93NUMPY" + b"\0" * 10_000_000)} for n in ("a.npy", "b.npy")]
    with pytest.raises(LabError) as error:
        lab.submit_experiment({"designs": bomb})
    assert "exceeds" in str(error.value)


def test_max_gates():
    metrics = evaluate_gates({"xor": 0.05, "ndcg": 0.4}, {"xor": {"max": 0.09}, "ndcg": 0.35})
    assert metrics["passed"] and metrics["gate_results"] == {"xor": True, "ndcg": True}
    assert not evaluate_gates({"xor": 0.1}, {"xor": {"max": 0.09}})["passed"]


def test_cli_upload_flow(campaign_path, tmp_path):
    lab = Lab(campaign_path, tmp_path / "state")
    server = HTTPServer(("127.0.0.1", 0), make_handler(lab))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ, "ASB_LAB_URL": f"http://127.0.0.1:{server.server_address[1]}"}

    def asb(*args, check=True):
        return subprocess.run([sys.executable, str(CLIENT), *args], env=env, text=True,
                              capture_output=True, check=check)

    try:
        a, b = tmp_path / "a.npy", tmp_path / "b.npy"
        np.save(a, np.eye(4, dtype=bool))                  # Boolean: normalized to uint8
        np.save(b, np.zeros((4, 4), dtype=np.float64))
        out = asb("run", str(a), str(b), "--poll-interval", "0.05", "-o", str(tmp_path / "m"))
        assert "stage 1 accepted: job-0001" in out.stdout
        assert (tmp_path / "m" / "a_sem.npy").exists() and (tmp_path / "m" / "b_sem.npy").exists()
        bad = tmp_path / "bad.npy"
        np.save(bad, np.full((4, 4), 0.5))
        rejected = asb("run", str(bad), str(b), check=False)
        assert rejected.returncode != 0 and "exactly 0 or 1" in rejected.stderr
        assert "no catalog" in asb("catalog", "--stage", "1", check=False).stderr
        assert "local validation passed" in asb("deliver", str(a), "--validate-only").stdout
        assert "submission 1" in asb("deliver", str(a)).stdout
        wrong = tmp_path / "wrong.npy"
        np.save(wrong, np.ones((3, 3), np.uint8))
        assert "shape (4, 4)" in asb("deliver", str(wrong), check=False).stderr
    finally:
        server.shutdown()


def test_catalog_card_is_unchanged(tmp_path):
    from conftest import write_campaign
    card = Lab(write_campaign(tmp_path / "c"), tmp_path / "s").campaign_card()
    assert set(card) == {"ok", "api_version", "campaign_id", "source", "objective",
                         "design_id_field", "measurements", "stages", "budget", "deliverable"}
    assert set(card["stages"][0]) == {"stage", "label", "batch_size", "catalog_size"}
    assert set(card["measurements"][0]) == {"name", "description", "higher_is_better"}
    assert set(card["deliverable"]) == {"kind", "format_version", "max_bytes", "input_field",
                                        "description", "requires"}
    assert card["deliverable"]["requires"] == "all stages completed"
