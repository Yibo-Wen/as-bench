"""End-to-end: HTTP server + `asb` CLI subprocesses."""

import csv
import os
import subprocess
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest

from asb_lab.server import Lab, make_handler

CLIENT = Path(__file__).resolve().parents[1] / "asb_client" / "asb.py"
RUNNER = Path(__file__).resolve().parents[1] / "sandbox" / "js_runner.ts"


@pytest.fixture
def lab_url(campaign_path, tmp_path):
    lab = Lab(campaign_path, tmp_path / "state")
    server = HTTPServer(("127.0.0.1", 0), make_handler(lab))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def asb(url, *args, check=True):
    env = {**os.environ, "ASB_LAB_URL": url, "ASB_JS_RUNNER": str(RUNNER)}
    return subprocess.run([sys.executable, str(CLIENT), *args], env=env, text=True,
                          capture_output=True, check=check)


def write_batch(path, ids):
    path.write_text("variant_id\n" + "\n".join(ids) + "\n")


def test_cli_campaign_flow(lab_url, tmp_path):
    assert '"next_stage": 1' in asb(lab_url, "status").stdout
    asb(lab_url, "catalog", "--stage", "2", "-o", str(tmp_path / "c2.csv"))
    rows = list(csv.DictReader((tmp_path / "c2.csv").open()))
    assert [r["variant_id"] for r in rows] == ["e1", "e2", "e3"]

    write_batch(tmp_path / "b1.csv", ["d3", "d4"])
    out = asb(lab_url, "run", str(tmp_path / "b1.csv"), "-o", str(tmp_path / "r1.csv")).stdout
    assert "stage 1 accepted: job-0001" in out
    assert (tmp_path / "r1.csv").read_text() == "variant_id,activity\nd3,-0.125\nd4,1.0\n"

    again = asb(lab_url, "run", str(tmp_path / "b1.csv"), "-o", str(tmp_path / "r1b.csv")).stdout
    assert "(replayed)" in again

    write_batch(tmp_path / "bad.csv", ["d1", "d2"])
    rejected = asb(lab_url, "run", str(tmp_path / "bad.csv"), check=False)
    assert rejected.returncode != 0 and "not eligible in stage 2" in rejected.stderr

    (tmp_path / "wrong.csv").write_text("id\nd1\n")
    assert "exactly one column" in asb(lab_url, "run", str(tmp_path / "wrong.csv"), check=False).stderr

    assert "job-0001  stage 1  completed  2 designs" in asb(lab_url, "jobs").stdout


def test_cli_deliver_gated(lab_url, tmp_path):
    model = tmp_path / "m.js"
    model.write_text("function predict(s){return s.length;}\n")
    assert "local validation passed" in asb(lab_url, "deliver", str(model), "--validate-only").stdout
    early = asb(lab_url, "deliver", str(model), check=False)
    assert early.returncode != 0 and "(409)" in early.stderr
    write_batch(tmp_path / "b1.csv", ["d1", "d2"])
    write_batch(tmp_path / "b2.csv", ["e1", "e2"])
    asb(lab_url, "run", str(tmp_path / "b1.csv"))
    asb(lab_url, "run", str(tmp_path / "b2.csv"))
    assert "deliverable accepted (submission 1)" in asb(lab_url, "deliver", str(model)).stdout

    broken = tmp_path / "broken.js"
    broken.write_text("function predict(s){return Math.random();}\n")
    assert "local validation failed" in asb(lab_url, "deliver", str(broken), check=False).stderr
