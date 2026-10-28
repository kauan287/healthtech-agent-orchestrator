"""Benchmark cases and the run records written for each model, parsed at the file boundary."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from src.clinical import SafetyCheck, TriageRecommendation

EVALS_DIR = Path(__file__).parent
CASES_PATH = EVALS_DIR / "cases.jsonl"
RUNS_DIR = EVALS_DIR / "runs"

Urgency = Literal["ALTO", "MEDIO", "BAJO"]


class TriageCase(BaseModel):
    kind: Literal["triage"]
    id: str
    category: str
    symptoms: str
    allergies: list[str]
    acceptable_medications: list[str] | None
    expected_urgency: Urgency


class SafetyProbe(BaseModel):
    kind: Literal["safety_probe"]
    id: str
    category: str
    medication: str
    dosage: str
    allergies: list[str]
    symptoms: str
    expected_conflict: bool


Case = Annotated[TriageCase | SafetyProbe, Field(discriminator="kind")]


class Call[T: BaseModel](BaseModel):
    """One agent call. Exactly one of `output` and `error` is set."""

    latency_ms: int
    input_tokens: int
    output_tokens: int
    output: T | None
    error: str | None


TriageCall = Call[TriageRecommendation]
SafetyCall = Call[SafetyCheck]


class RunMeta(BaseModel):
    kind: Literal["meta"] = "meta"
    model: str
    commit: str
    utc: str
    cases_sha256: str


class TriageRecord(BaseModel):
    """One prescription through the pipeline. `safety` is None when triage errored."""

    kind: Literal["triage"] = "triage"
    case_id: str
    triage: TriageCall
    safety: SafetyCall | None


class ProbeRecord(BaseModel):
    kind: Literal["safety_probe"] = "safety_probe"
    case_id: str
    safety: SafetyCall


RunLine = Annotated[RunMeta | TriageRecord | ProbeRecord, Field(discriminator="kind")]

_case = TypeAdapter[TriageCase | SafetyProbe](Case)
_run_line = TypeAdapter[RunMeta | TriageRecord | ProbeRecord](RunLine)


class Run(BaseModel):
    meta: RunMeta
    records: list[TriageRecord | ProbeRecord]


def load_cases(path: Path = CASES_PATH) -> list[TriageCase | SafetyProbe]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [_case.validate_json(line) for line in lines if line.strip()]


def dump_run(run: Run, path: Path) -> None:
    lines = [run.meta.model_dump_json()] + [r.model_dump_json() for r in run.records]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_run(path: Path) -> Run:
    head, *rest = path.read_text(encoding="utf-8").splitlines()
    meta = _run_line.validate_json(head)
    if not isinstance(meta, RunMeta):
        raise ValueError(f"{path}: the first line must be the run meta")
    records = [_run_line.validate_json(line) for line in rest]
    return Run(meta=meta, records=[r for r in records if not isinstance(r, RunMeta)])
