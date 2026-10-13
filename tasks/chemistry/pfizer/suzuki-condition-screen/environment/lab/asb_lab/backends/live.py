# Vendored from runtime/asb_lab/backends/live.py by tools/vendor_runtime.py. Do not edit; change the source and re-run.
"""Live lab backend: a physical laboratory measures the batch.

Two ways to reach a laboratory, selected by the campaign's ``backend`` config.

``endpoint``
    The lab's own job API, reachable only from the lab container. Credentials come from
    lab-container environment variables, never files in the agent image. Not implemented
    here: every laboratory's API differs, so the adapter belongs with the laboratory.

``inbox``
    A directory the laboratory shares with the lab container, and the protocol is files.
    An absolute path is used as given; a relative one resolves against the lab's state
    directory, so ``"live"`` means ``<state>/live`` wherever the lab is running.
    This is the human-in-the-loop case, and it is the one most wet rounds start as: no
    API, a person reads a batch and types results back. ``submit`` writes the batch and
    returns ``queued``; ``poll`` looks for what the laboratory wrote back.

      <inbox>/<job_id>/batch.csv     written by the lab: the designs, in the order the
                                     agent ordered them, one per line after the header
      <inbox>/<job_id>/request.json  written by the lab: stage, measurement names, and
                                     the schema the results must satisfy
      <inbox>/<job_id>/running       optional, written by the laboratory: the plate is on
                                     the instrument, so the job reports ``running``
      <inbox>/<job_id>/results.csv   written by the laboratory: <design_id_field> plus one
                                     column per measurement, one row per design
      <inbox>/<job_id>/failed        written by the laboratory: the first line is why; the
                                     job fails without consuming its stage, so the agent
                                     may resubmit

Wet-lab latency, hours to days, is absorbed by the job model: the agent sees the same
queued / running / completed sequence it sees from a digital twin, and ``asb run
--no-wait`` lets it stop waiting. Nothing about the agent-facing API changes.

A results file that does not cover every design in the batch, or that carries a value
that is not a finite number, fails the job rather than completing it with a hole in it.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from .base import COMPLETED, FAILED, QUEUED, RUNNING, Backend, JobUpdate


class LiveBackend(Backend):
    kind = "live"

    def __init__(self, campaign, config, root) -> None:
        super().__init__(campaign, config, root)
        self.endpoint = config.get("endpoint")
        inbox = config.get("inbox")
        self.inbox = Path(inbox) if inbox else None

    def _unavailable(self) -> NotImplementedError:
        """Neither route configured, which the server turns into 503.

        Constructing still has to succeed. A laboratory that cannot take work is a state
        the API has, not a configuration error: the server answers 503, the stage is not
        consumed, nothing reaches the ledger, and the agent may try again when the
        instrument is back. Raising here would instead stop the lab from starting at all.
        """
        return NotImplementedError(
            "no laboratory route configured: set backend.inbox for the file-drop "
            "protocol, or backend.endpoint with the laboratory's own adapter")

    # -- the file-drop protocol ------------------------------------------------------
    def _job_dir(self, job_id: str, output_dir: Path) -> Path:
        """Where this job's files live.

        ``output_dir`` is ``<state>/jobs/<job_id>``, which is how a relative inbox finds
        the state directory without the backend having to be told where it is. The
        resolved path goes back to the server as ``backend_ref``, the contract's opaque
        polling handle, so ``poll`` needs nothing but the job.
        """
        base = self.inbox if self.inbox.is_absolute() else output_dir.parents[1] / self.inbox
        return base / job_id

    def submit(self, job_id: str, stage: int, designs: list[str],
               uploads=None, output_dir=None) -> JobUpdate:
        if self.inbox is None:
            raise self._unavailable() if not self.endpoint else NotImplementedError(
                "live backend with an endpoint needs the laboratory's own adapter")
        directory = self._job_dir(job_id, Path(output_dir))
        directory.mkdir(parents=True, exist_ok=True)
        id_field = self.campaign.design_id_field
        names = [m.name for m in self.campaign.measurements]
        with (directory / "batch.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([id_field])
            writer.writerows([[design] for design in designs])
        (directory / "request.json").write_text(json.dumps({
            "job_id": job_id,
            "stage": stage,
            "campaign_id": self.campaign.campaign_id,
            "label": self.config.get("label", ""),
            "n_designs": len(designs),
            "order_is_the_ranking": True,
            "results_schema": {"file": "results.csv",
                               "columns": [id_field, *names],
                               "rows": "one per design in batch.csv"},
        }, indent=2) + "\n")
        return JobUpdate(status=QUEUED, backend_ref=str(directory))

    def poll(self, job: dict) -> JobUpdate:
        if self.inbox is None:
            raise self._unavailable() if not self.endpoint else NotImplementedError(
                "live backend with an endpoint needs the laboratory's own adapter")
        reference = job.get("backend_ref")
        if not reference:
            return JobUpdate(status=FAILED, error="the job has no laboratory reference")
        directory = Path(reference)
        failed = directory / "failed"
        if failed.is_file():
            reason = failed.read_text().strip().splitlines()
            return JobUpdate(status=FAILED,
                             error=reason[0] if reason else "the laboratory failed the job")
        results = directory / "results.csv"
        if not results.is_file():
            running = (directory / "running").is_file()
            return JobUpdate(status=RUNNING if running else QUEUED, backend_ref=reference)
        try:
            rows = self._read_results(results, job["designs"])
        except ValueError as error:
            return JobUpdate(status=FAILED, error=str(error))
        return JobUpdate(status=COMPLETED, results=rows)

    def _read_results(self, path: Path, designs: list[str]) -> list[dict]:
        id_field = self.campaign.design_id_field
        names = [m.name for m in self.campaign.measurements]
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            missing = {id_field, *names} - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"results.csv lacks columns {sorted(missing)}")
            by_design: dict[str, dict] = {}
            for row in reader:
                design = (row[id_field] or "").strip()
                if design in by_design:
                    raise ValueError(f"results.csv repeats {design}")
                values = {}
                for name in names:
                    try:
                        value = float(row[name])
                    except (TypeError, ValueError):
                        raise ValueError(f"results.csv has no number for "
                                         f"{design} {name}") from None
                    if not math.isfinite(value):
                        raise ValueError(f"results.csv value for {design} {name} "
                                         f"is not finite")
                    values[name] = value
                by_design[design] = values
        unmeasured = [design for design in designs if design not in by_design]
        if unmeasured:
            raise ValueError(f"results.csv is missing {len(unmeasured)} designs, "
                             f"e.g. {unmeasured[0]}")
        return [{id_field: design, **by_design[design]} for design in designs]
