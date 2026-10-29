"""Write evals/REPORT.md from every run in evals/runs/.

uv run python -m evals.report
"""

from collections.abc import Sequence
from pathlib import Path

from evals.metrics import Latency, ModelMetrics, load_pricing, score
from evals.records import CASES_PATH, EVALS_DIR, RUNS_DIR, Run, load_cases, load_run

REPORT_PATH = EVALS_DIR / "REPORT.md"

METHOD = """\
Each model runs the same 48 cases through the code that production runs.
The 24 triage cases go through the pipeline: the triage agent proposes a medication,
then the safety agent checks that proposal against the patient's allergies.
The 24 safety probes call the safety agent alone with a fixed medication.
`evals/reference.py` decides the ground truth for allergies from the families in
`evals/allergy_families.json`. An allergy to one member blocks the whole family.

Safety recall is the main number. A missed allergy is the dangerous error.
An escape is a proposal that conflicts with an allergy and that the model's own safety
check passed, so it would reach the doctor unflagged. Every rate shows its numerator and
denominator. A call that failed counts in the error rate and is left out of the quality rates.

**Disclaimer.** The author wrote these cases and labels. This is an engineering benchmark,
not a clinical validation, and its rule table is not medical advice."""


def _latency(latency: Latency) -> str:
    if latency.p50_ms is None:
        return "n/a"
    return f"{latency.p50_ms} / {latency.p95_ms}"


def _tokens(metrics: ModelMetrics) -> str:
    t = metrics.mean_tokens_per_prescription
    return "n/a" if t is None else f"{t.input:.0f} in, {t.output:.0f} out"


def _cost(metrics: ModelMetrics) -> str:
    return "n/a" if metrics.cost_per_1k_usd is None else f"${metrics.cost_per_1k_usd:.4f}"


def render(runs: Sequence[tuple[Run, ModelMetrics]]) -> str:
    lines = ["# Agent evaluation", "", METHOD, "", "## Runs", ""]
    lines += ["| Model | Commit | Date (UTC) | Cases file SHA-256 |", "|---|---|---|---|"]
    for run, _ in runs:
        m = run.meta
        lines.append(f"| `{m.model}` | `{m.commit}` | {m.utc} | `{m.cases_sha256[:12]}` |")

    lines += ["", "## Summary", ""]
    lines += [
        "| Model | Safety recall | Specificity | Escapes | Acceptable proposal | Allergen avoided "
        "| Urgency accuracy | Red flags missed | Error rate | Triage ms p50 / p95 "
        "| Safety ms p50 / p95 | Tokens per prescription | Cost per 1,000 prescriptions |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for _, x in runs:
        lines.append(
            f"| `{x.model}` | {x.safety.recall} | {x.safety.specificity} | {x.escapes} "
            f"| {x.proposal_acceptable} | {x.allergen_avoided} | {x.urgency_accuracy} "
            f"| {x.red_flags_missed} | {x.errors} | {_latency(x.triage_latency)} "
            f"| {_latency(x.safety_latency)} | {_tokens(x)} | {_cost(x)} |"
        )

    lines += ["", "## Safety recall by probe category", ""]
    categories = sorted({c for _, x in runs for c in x.safety_by_category})
    lines += [
        "| Category | " + " | ".join(f"`{x.model}`" for _, x in runs) + " |",
        "|---|" + "---|" * len(runs),
    ]
    for category in categories:
        cells = []
        for _, x in runs:
            confusion = x.safety_by_category.get(category)
            if confusion is None:
                cells.append("n/a")
            elif confusion.tp + confusion.fn == 0:
                cells.append(f"no conflicts, specificity {confusion.specificity}")
            else:
                cells.append(str(confusion.recall))
        lines.append(f"| {category} | " + " | ".join(cells) + " |")

    lines += ["", "## Failures", ""]
    for _, x in runs:
        lines += [f"### `{x.model}`", ""]
        if x.red_flags_missed_ids:
            lines += [f"Red flags under-triaged: {', '.join(x.red_flags_missed_ids)}.", ""]
        lines += [f"- **{f.case_id}.** {f.explanation}" for f in x.failures] or ["None."]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build(runs_dir: Path = RUNS_DIR, cases_path: Path = CASES_PATH) -> str:
    cases = load_cases(cases_path)
    pricing = load_pricing()
    runs = [load_run(path) for path in sorted(runs_dir.glob("*.jsonl"))]
    return render([(run, score(run, cases, pricing.get(run.meta.model))) for run in runs])


def main() -> None:
    REPORT_PATH.write_text(build(), encoding="utf-8")
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
