"""Fail when the demo agents answer differently from the committed evals/runs/demo.jsonl.

Latency is left out because it varies between runs. Token counts and outputs are compared.
uv run python -m evals.drift
"""

from evals.records import (
    CASES_PATH,
    RUNS_DIR,
    ProbeRecord,
    Run,
    SafetyCall,
    TriageCall,
    load_cases,
    load_run,
)
from evals.run import Caller, make_meta, run_model
from src.clinical import DemoClinicalModel

COMMITTED = RUNS_DIR / "demo.jsonl"


def _answer(call: TriageCall | SafetyCall | None) -> str:
    return "none" if call is None else call.model_copy(update={"latency_ms": 0}).model_dump_json()


def answers(run: Run) -> list[tuple[str, list[str]]]:
    return [
        (
            r.case_id,
            [_answer(r.safety)]
            if isinstance(r, ProbeRecord)
            else [_answer(r.triage), _answer(r.safety)],
        )
        for r in run.records
    ]


def main() -> None:
    committed = load_run(COMMITTED)
    fresh = run_model(
        "demo",
        DemoClinicalModel(),
        load_cases(CASES_PATH),
        Caller(rpm=None),
        make_meta("demo", CASES_PATH),
        log=lambda _: None,
    )
    changed = [
        case_id
        for (case_id, new), (_, old) in zip(answers(fresh), answers(committed), strict=True)
        if new != old
    ]
    if changed:
        raise SystemExit(
            f"The demo agents now answer differently for {', '.join(changed)}. "
            "Run `make eval-demo` and commit evals/runs/demo.jsonl and evals/REPORT.md."
        )
    print(f"demo answers match {COMMITTED.name} on all {len(committed.records)} cases")


if __name__ == "__main__":
    main()
