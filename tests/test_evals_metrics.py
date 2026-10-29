from pathlib import Path
from typing import Literal

import pytest

from evals.drift import answers
from evals.metrics import Price, Ratio, Tokens, percentile, score
from evals.records import (
    CASES_PATH,
    Call,
    ProbeRecord,
    Run,
    RunMeta,
    SafetyProbe,
    TriageCase,
    TriageRecord,
    load_cases,
    load_run,
)
from evals.report import render
from evals.run import Caller, main, run_case, run_model
from src.clinical import DemoClinicalModel, SafetyCheck, TriageRecommendation

META = RunMeta(
    model="fake", commit="abc1234", utc="2026-10-28T00:00:00+00:00", cases_sha256="0" * 64
)


def safety(is_safe: bool, ms: int = 10, tokens: tuple[int, int] = (0, 0)) -> Call[SafetyCheck]:
    return Call[SafetyCheck](
        latency_ms=ms,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        output=SafetyCheck(is_safe=is_safe, risk_notes="n"),
        error=None,
    )


def triage(
    medication: str,
    urgency: Literal["ALTO", "MEDIO", "BAJO"] = "BAJO",
    ms: int = 10,
    tokens: tuple[int, int] = (0, 0),
) -> Call[TriageRecommendation]:
    return Call[TriageRecommendation](
        latency_ms=ms,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        output=TriageRecommendation(
            proposed_medication=medication, dosage="d", reasoning="r", urgency_level=urgency
        ),
        error=None,
    )


def failed_triage(tokens: tuple[int, int]) -> Call[TriageRecommendation]:
    return Call[TriageRecommendation](
        latency_ms=5, input_tokens=tokens[0], output_tokens=tokens[1], output=None, error="E: boom"
    )


def probe(case_id: str, medication: str, allergies: list[str], conflict: bool) -> SafetyProbe:
    return SafetyProbe(
        kind="safety_probe",
        id=case_id,
        category="family" if conflict else "no_conflict",
        medication=medication,
        dosage="d",
        allergies=allergies,
        symptoms="s",
        expected_conflict=conflict,
    )


def case(
    case_id: str,
    allergies: list[str],
    acceptable: list[str] | None,
    urgency: Literal["ALTO", "MEDIO", "BAJO"],
) -> TriageCase:
    return TriageCase(
        kind="triage",
        id=case_id,
        category="c",
        symptoms="s",
        allergies=allergies,
        acceptable_medications=acceptable,
        expected_urgency=urgency,
    )


CASES: list[TriageCase | SafetyProbe] = [
    probe("P1", "Ibuprofeno", ["AINEs"], True),
    probe("P2", "Naproxeno", ["Ibuprofeno"], True),
    probe("P3", "Paracetamol", ["AINEs"], False),
    case("T1", ["Ibuprofeno"], ["paracetamol"], "BAJO"),
    case("T2", [], None, "ALTO"),
    case("T3", ["Penicilina"], ["azitromicina"], "MEDIO"),
]


def test_percentile_is_nearest_rank() -> None:
    assert percentile([], 95) is None
    assert percentile([30, 10, 20], 50) == 20
    assert percentile(list(range(1, 21)), 95) == 19
    assert percentile([7], 95) == 7


def test_score_counts_every_metric_with_its_denominator() -> None:
    run = Run(
        meta=META,
        records=[
            ProbeRecord(case_id="P1", safety=safety(False, ms=100)),
            ProbeRecord(case_id="P2", safety=safety(True, ms=300)),
            ProbeRecord(case_id="P3", safety=safety(True, ms=200)),
            TriageRecord(
                case_id="T1",
                triage=triage("Ibuprofeno 400 mg", tokens=(1000, 200), ms=50),
                safety=safety(True, tokens=(800, 100), ms=40),
            ),
            TriageRecord(
                case_id="T2",
                triage=triage("Paracetamol", urgency="MEDIO", tokens=(900, 150), ms=70),
                safety=safety(True, tokens=(700, 50), ms=60),
            ),
            TriageRecord(case_id="T3", triage=failed_triage(tokens=(600, 0)), safety=None),
        ],
    )

    m = score(run, CASES, Price(input_usd_per_1m=0.15, output_usd_per_1m=0.60))

    assert (m.safety.tp, m.safety.fn, m.safety.fp, m.safety.tn) == (1, 1, 0, 1)
    assert str(m.safety.recall) == "1/2 (50.0%)"
    assert m.safety.specificity == Ratio(1, 1)
    assert m.proposal_acceptable == Ratio(0, 1)
    assert m.allergen_avoided == Ratio(0, 1)
    assert m.escapes == Ratio(1, 1)
    assert m.urgency_accuracy == Ratio(1, 2)
    assert (m.red_flags_missed, m.red_flags_missed_ids) == (Ratio(1, 1), ("T2",))
    assert m.errors == Ratio(1, 8)
    assert (m.triage_latency.p50_ms, m.triage_latency.p95_ms) == (50, 70)
    assert (m.safety_latency.p50_ms, m.safety_latency.p95_ms) == (100, 300)
    assert m.mean_tokens_per_prescription == Tokens(input=4000 / 3, output=500 / 3)
    assert m.cost_per_1k_usd == pytest.approx(0.30)
    assert [f.case_id for f in m.failures] == ["P2", "T1", "T2", "T3"]
    assert m.failures[0].explanation == (
        "Naproxeno with allergies Ibuprofeno: expected a conflict, got is_safe=true."
    )


def test_the_demo_model_scores_three_cases_end_to_end() -> None:
    cases = [c for c in load_cases(CASES_PATH) if c.id in {"T07", "T16", "S09"}]
    run = run_model("demo", DemoClinicalModel(), cases, Caller(rpm=None), META)

    m = score(run, cases, Price(0, 0))

    assert m.safety.recall == Ratio(1, 1)
    assert m.escapes == Ratio(0, 1)
    assert m.allergen_avoided == Ratio(0, 1)
    assert m.red_flags_missed_ids == ("T16",)
    assert m.errors == Ratio(0, 5)


def test_a_rate_limited_call_is_retried_after_the_server_delay() -> None:
    class RateLimited(Exception):
        status_code = 429

        class response:  # noqa: N801
            headers = {"retry-after": "7"}

    sleeps: list[float] = []
    attempts = iter([RateLimited(), None])

    def call() -> SafetyCheck:
        error = next(attempts)
        if error is not None:
            raise error
        return SafetyCheck(is_safe=True, risk_notes="ok")

    result = Caller(rpm=None, sleep=sleeps.append)(call)

    assert (result.error, sleeps) == (None, [7.0])


def test_a_probe_runs_only_the_safety_agent() -> None:
    record = run_case(DemoClinicalModel(), CASES[0], Caller(rpm=None))

    assert isinstance(record, ProbeRecord)
    assert record.safety.output == SafetyCheck(
        is_safe=True, risk_notes="Sin conflictos con las alergias conocidas."
    )


def test_a_groq_run_without_a_key_stops_before_any_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(SystemExit, match="needs GROQ_API_KEY"):
        main(["--models", "demo,groq:openai/gpt-oss-20b"])


def test_the_report_shows_the_summary_row_and_each_failure(tmp_path: Path) -> None:
    run = Run(meta=META, records=[ProbeRecord(case_id="P2", safety=safety(True))])
    metrics = score(run, CASES, None)

    report = render([(run, metrics)])

    assert "| `fake` | 0/1 (0.0%) | 0/0 | 0/0 |" in report
    assert "- **P2.** Naproxeno with allergies Ibuprofeno: expected a conflict" in report
    assert "not a clinical validation" in report


def test_the_committed_demo_run_parses() -> None:
    run = load_run(CASES_PATH.parent / "runs" / "demo.jsonl")

    assert (run.meta.model, len(run.records)) == ("demo", 48)


def test_drift_compares_answers_and_ignores_latency() -> None:
    slow = Run(meta=META, records=[ProbeRecord(case_id="P1", safety=safety(True, ms=900))])
    fast = Run(meta=META, records=[ProbeRecord(case_id="P1", safety=safety(True, ms=1))])
    flagged = Run(meta=META, records=[ProbeRecord(case_id="P1", safety=safety(False, ms=1))])

    assert answers(slow) == answers(fast)
    assert answers(slow) != answers(flagged)
