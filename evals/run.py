"""Run the clinical agents over the benchmark and write one JSONL per model to evals/runs/.

uv run python -m evals.run --models demo,groq:openai/gpt-oss-120b --rpm 20
"""

import argparse
import hashlib
import os
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.callbacks import get_usage_metadata_callback
from pydantic import BaseModel

from evals.records import (
    CASES_PATH,
    RUNS_DIR,
    Call,
    ProbeRecord,
    Run,
    RunMeta,
    SafetyProbe,
    TriageCase,
    TriageRecord,
    dump_run,
    load_cases,
)
from src.clinical import ClinicalModel, DemoClinicalModel, GroqClinicalModel

MAX_RETRIES = 5
GROQ_DEFAULT_RPM = 20.0


def build_model(spec: str) -> ClinicalModel:
    if spec == "demo":
        return DemoClinicalModel()
    if spec.startswith("groq:"):
        key = os.environ.get("GROQ_API_KEY", "")
        if not key:
            raise SystemExit(f"{spec} needs GROQ_API_KEY in the environment.")
        return GroqClinicalModel(api_key=key, model=spec.removeprefix("groq:"))
    raise ValueError(f"Unknown model {spec!r}. Use demo or groq:<model id>.")


def slug(spec: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", spec.lower()).strip("-")


def _retry_after_s(error: Exception, attempt: int) -> float:
    response = getattr(error, "response", None)
    header = response.headers.get("retry-after") if response is not None else None
    try:
        return float(header) if header is not None else 2.0**attempt
    except ValueError:
        return 2.0**attempt


class Caller:
    """Times one agent call, counts its tokens, paces calls to `rpm`, and retries HTTP 429."""

    def __init__(self, rpm: float | None, sleep: Callable[[float], None] = time.sleep) -> None:
        self._interval_s = 60.0 / rpm if rpm else 0.0
        self._sleep = sleep
        self._next_start = 0.0

    def _pace(self) -> None:
        wait = self._next_start - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._next_start = time.monotonic() + self._interval_s

    def __call__[T: BaseModel](self, call: Callable[[], T]) -> Call[T]:
        for attempt in range(MAX_RETRIES + 1):
            self._pace()
            with get_usage_metadata_callback() as usage:
                start = time.perf_counter()
                try:
                    output: T | None = call()
                    error: Exception | None = None
                except Exception as exc:
                    output, error = None, exc
                latency_ms = round((time.perf_counter() - start) * 1000)
            rate_limited = getattr(error, "status_code", None) == 429
            if error is not None and rate_limited and attempt < MAX_RETRIES:
                self._sleep(_retry_after_s(error, attempt))
                continue
            return Call[T](
                latency_ms=latency_ms,
                input_tokens=sum(u["input_tokens"] for u in usage.usage_metadata.values()),
                output_tokens=sum(u["output_tokens"] for u in usage.usage_metadata.values()),
                output=output,
                error=None if error is None else f"{type(error).__name__}: {error}"[:300],
            )
        raise AssertionError("unreachable")


def run_case(
    model: ClinicalModel, case: TriageCase | SafetyProbe, caller: Caller
) -> TriageRecord | ProbeRecord:
    """Exactly what production runs: triage then a safety check of that proposal, or a probe."""
    if isinstance(case, SafetyProbe):
        probe = case
        return ProbeRecord(
            case_id=case.id,
            safety=caller(
                lambda: model.check_safety(
                    probe.medication, probe.dosage, probe.symptoms, probe.allergies
                )
            ),
        )
    patient = case
    triage = caller(lambda: model.triage(patient.symptoms, patient.allergies))
    proposal = triage.output
    if proposal is None:
        return TriageRecord(case_id=case.id, triage=triage, safety=None)
    safety = caller(
        lambda: model.check_safety(
            proposal.proposed_medication, proposal.dosage, patient.symptoms, patient.allergies
        )
    )
    return TriageRecord(case_id=case.id, triage=triage, safety=safety)


def run_model(
    spec: str,
    model: ClinicalModel,
    cases: Sequence[TriageCase | SafetyProbe],
    caller: Caller,
    meta: RunMeta,
    log: Callable[[str], None] = print,
) -> Run:
    records = []
    for n, case in enumerate(cases, 1):
        record = run_case(model, case, caller)
        calls = (
            [record.safety] if isinstance(record, ProbeRecord) else [record.triage, record.safety]
        )
        failed = [c.error for c in calls if c is not None and c.error]
        log(f"[{spec}] {n}/{len(cases)} {case.id}" + (f" ERROR {failed[0]}" if failed else ""))
        records.append(record)
    return Run(meta=meta, records=records)


def _commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "unknown"


def make_meta(spec: str, cases_path: Path) -> RunMeta:
    return RunMeta(
        model=spec,
        commit=_commit(),
        utc=datetime.now(UTC).isoformat(timespec="seconds"),
        cases_sha256=hashlib.sha256(cases_path.read_bytes()).hexdigest(),
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default="demo", help="comma list: demo, groq:<model id>")
    parser.add_argument("--rpm", type=float, default=GROQ_DEFAULT_RPM, help="groq calls/minute")
    parser.add_argument("--only", default="", help="comma list of case ids")
    args = parser.parse_args(argv)

    cases = load_cases(CASES_PATH)
    only = {i.strip() for i in args.only.split(",") if i.strip()}
    if only:
        unknown = only - {c.id for c in cases}
        if unknown:
            raise SystemExit(f"Unknown case ids: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c.id in only]
    specs = [m.strip() for m in args.models.split(",") if m.strip()]
    models = {spec: build_model(spec) for spec in specs}
    for spec, model in models.items():
        caller = Caller(rpm=None if spec == "demo" else args.rpm)
        run = run_model(spec, model, cases, caller, make_meta(spec, CASES_PATH))
        path = RUNS_DIR / f"{slug(spec)}.jsonl"
        dump_run(run, path)
        print(f"[{spec}] wrote {path}")


if __name__ == "__main__":
    main()
