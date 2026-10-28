"""Score one model's run against the labelled cases. Pure functions over parsed records.

A call that errored is left out of the quality metrics and counted in the error rate,
so every rate shows its own denominator.
"""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evals.records import EVALS_DIR, ProbeRecord, Run, SafetyProbe, TriageCase, TriageRecord
from evals.reference import conflicts, matches_any

PRICING_PATH = EVALS_DIR / "pricing.json"


@dataclass(frozen=True)
class Ratio:
    num: int
    den: int

    def __str__(self) -> str:
        if self.den == 0:
            return "0/0"
        return f"{self.num}/{self.den} ({100 * self.num / self.den:.1f}%)"


@dataclass(frozen=True)
class Confusion:
    """Positive = the medication conflicts with an allergy. Predicted positive = is_safe is False."""

    tp: int = 0
    fn: int = 0
    fp: int = 0
    tn: int = 0

    @property
    def recall(self) -> Ratio:
        return Ratio(self.tp, self.tp + self.fn)

    @property
    def specificity(self) -> Ratio:
        return Ratio(self.tn, self.tn + self.fp)

    def add(self, expected: bool, predicted: bool) -> "Confusion":
        return Confusion(
            tp=self.tp + (expected and predicted),
            fn=self.fn + (expected and not predicted),
            fp=self.fp + (not expected and predicted),
            tn=self.tn + (not expected and not predicted),
        )


@dataclass(frozen=True)
class Latency:
    p50_ms: int | None
    p95_ms: int | None


@dataclass(frozen=True)
class Price:
    input_usd_per_1m: float
    output_usd_per_1m: float


@dataclass(frozen=True)
class Failure:
    case_id: str
    explanation: str


@dataclass(frozen=True)
class ModelMetrics:
    model: str
    safety: Confusion
    safety_by_category: dict[str, Confusion]
    proposal_acceptable: Ratio
    allergen_avoided: Ratio
    escapes: Ratio  # of the proposals that conflict and got a safety verdict
    urgency_accuracy: Ratio
    red_flags_missed: Ratio
    red_flags_missed_ids: tuple[str, ...]
    errors: Ratio  # failed calls / calls
    triage_latency: Latency
    safety_latency: Latency
    mean_tokens_per_prescription: tuple[float, float] | None  # (input, output)
    cost_per_1k_usd: float | None
    failures: tuple[Failure, ...]


def percentile(values: Sequence[int], p: float) -> int | None:
    """Nearest-rank percentile: the smallest observed value with at least p% of values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(p / 100 * len(ordered)), 1) - 1]


def load_pricing(path: Path = PRICING_PATH) -> dict[str, Price]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {model: Price(**price) for model, price in raw["models"].items()}


def _allergy_text(allergies: list[str]) -> str:
    return ", ".join(allergies) if allergies else "none"


def score(run: Run, cases: Sequence[TriageCase | SafetyProbe], price: Price | None) -> ModelMetrics:
    by_id = {c.id: c for c in cases}
    safety = Confusion()
    by_category: dict[str, Confusion] = {}
    acceptable = avoided = conflicting = escapes = urgency_ok = 0
    scored = with_allergies = triaged = alto = 0
    missed_red_flags: list[str] = []
    calls = failed_calls = 0
    triage_ms: list[int] = []
    safety_ms: list[int] = []
    prescription_tokens: list[tuple[int, int]] = []
    failures: list[Failure] = []

    for record in run.records:
        case = by_id[record.case_id]
        calls_in_record = [record.safety] if isinstance(record, ProbeRecord) else [record.triage]
        if isinstance(record, TriageRecord) and record.safety is not None:
            calls_in_record.append(record.safety)
        for call in calls_in_record:
            calls += 1
            failed_calls += call.error is not None
        if isinstance(record, ProbeRecord) and isinstance(case, SafetyProbe):
            if record.safety.output is None:
                failures.append(Failure(case.id, f"safety call failed: {record.safety.error}"))
                continue
            safety_ms.append(record.safety.latency_ms)
            flagged = not record.safety.output.is_safe
            safety = safety.add(case.expected_conflict, flagged)
            by_category[case.category] = by_category.get(case.category, Confusion()).add(
                case.expected_conflict, flagged
            )
            if flagged != case.expected_conflict:
                expected = "a conflict" if case.expected_conflict else "no conflict"
                failures.append(
                    Failure(
                        case.id,
                        f"{case.medication} with allergies {_allergy_text(case.allergies)}: "
                        f"expected {expected}, got is_safe={str(not flagged).lower()}.",
                    )
                )
            continue

        assert isinstance(record, TriageRecord) and isinstance(case, TriageCase)
        proposal = record.triage.output
        if proposal is None:
            failures.append(Failure(case.id, f"triage call failed: {record.triage.error}"))
            continue
        triaged += 1
        triage_ms.append(record.triage.latency_ms)
        medication = proposal.proposed_medication
        issues: list[str] = []

        if case.acceptable_medications is not None:
            scored += 1
            if matches_any(medication, case.acceptable_medications):
                acceptable += 1
            else:
                issues.append(
                    f"proposed {medication}, acceptable: {', '.join(case.acceptable_medications)}"
                )

        check = record.safety.output if record.safety else None
        conflict = conflicts(medication, case.allergies)
        if case.allergies:
            with_allergies += 1
            avoided += not conflict
        if conflict:
            if check is None:
                verdict = f"the safety call failed: {record.safety.error if record.safety else ''}"
            elif check.is_safe:
                conflicting += 1
                escapes += 1
                verdict = "the safety check passed it (escape)"
            else:
                conflicting += 1
                verdict = "the safety check flagged it"
            issues.append(
                f"proposed {medication} despite allergies {_allergy_text(case.allergies)}; {verdict}"
            )
        elif check is not None and not check.is_safe:
            issues.append(f"the safety check flagged {medication}, which the reference finds safe")
        elif check is None and record.safety is not None:
            issues.append(f"the safety call failed: {record.safety.error}")

        if proposal.urgency_level == case.expected_urgency:
            urgency_ok += 1
        else:
            issues.append(f"urgency {proposal.urgency_level}, expected {case.expected_urgency}")
        if case.expected_urgency == "ALTO":
            alto += 1
            if proposal.urgency_level != "ALTO":
                missed_red_flags.append(case.id)

        if record.safety is not None and record.safety.output is not None:
            safety_ms.append(record.safety.latency_ms)
            prescription_tokens.append(
                (
                    record.triage.input_tokens + record.safety.input_tokens,
                    record.triage.output_tokens + record.safety.output_tokens,
                )
            )
        if issues:
            failures.append(Failure(case.id, "; ".join(issues) + "."))

    mean_tokens = None
    cost = None
    if prescription_tokens:
        n = len(prescription_tokens)
        mean_tokens = (
            sum(t[0] for t in prescription_tokens) / n,
            sum(t[1] for t in prescription_tokens) / n,
        )
        if price is not None:
            cost = (
                1000
                * (
                    mean_tokens[0] * price.input_usd_per_1m
                    + mean_tokens[1] * price.output_usd_per_1m
                )
                / 1_000_000
            )

    return ModelMetrics(
        model=run.meta.model,
        safety=safety,
        safety_by_category=dict(sorted(by_category.items())),
        proposal_acceptable=Ratio(acceptable, scored),
        allergen_avoided=Ratio(avoided, with_allergies),
        escapes=Ratio(escapes, conflicting),
        urgency_accuracy=Ratio(urgency_ok, triaged),
        red_flags_missed=Ratio(len(missed_red_flags), alto),
        red_flags_missed_ids=tuple(missed_red_flags),
        errors=Ratio(failed_calls, calls),
        triage_latency=Latency(percentile(triage_ms, 50), percentile(triage_ms, 95)),
        safety_latency=Latency(percentile(safety_ms, 50), percentile(safety_ms, 95)),
        mean_tokens_per_prescription=mean_tokens,
        cost_per_1k_usd=cost,
        failures=tuple(failures),
    )
