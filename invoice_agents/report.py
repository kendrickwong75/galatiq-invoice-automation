"""Stakeholder-facing terminal output (rich) and the batch summary / business-impact numbers."""

from __future__ import annotations

from collections import Counter
from statistics import median

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import config
from .models import InvoiceResult, Outcome, Severity

STYLE = {Outcome.APPROVED: "green", Outcome.NEEDS_HUMAN_REVIEW: "yellow", Outcome.REJECTED: "red"}
LABEL = {Outcome.APPROVED: "APPROVED & PAID", Outcome.NEEDS_HUMAN_REVIEW: "NEEDS HUMAN REVIEW",
         Outcome.REJECTED: "REJECTED"}
SEV_STYLE = {Severity.REJECT: "red", Severity.REVIEW: "yellow", Severity.FLAG: "magenta", Severity.INFO: "dim"}


def _usd(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"-${-v:,.2f}" if v < 0 else f"${v:,.2f}"


def invoice_panel(r: InvoiceResult) -> Panel:
    inv = r.invoice
    head = Table.grid(padding=(0, 2))
    head.add_column(style="bold")
    head.add_column()
    if inv:
        head.add_row("Vendor", inv.vendor or "[red](missing)[/red]")
        head.add_row("Invoice #", inv.invoice_number or "[red](missing)[/red]")
        head.add_row("Dates", f"issued {inv.invoice_date or '?'}  ·  due {inv.due_date or '?'}")
        amount = _usd(r.amount_usd)
        if inv.currency != "USD" and r.fx_rate:
            amount += f"  ({inv.total:,.2f} {inv.currency} @ {r.fx_rate})"
        head.add_row("Amount", amount + ("  [bold yellow]VP review (> $10K)[/]" if r.vp_review else ""))
        head.add_row("Items", ", ".join(f"{li.item} ×{li.quantity:g}" + (f" ({li.note})" if li.note else "")
                                        for li in inv.line_items) or "(none)")
    agent = (f"extraction: {r.extraction_method} ×{r.extraction_attempts}  ·  tools: "
             f"{len(set(r.validator_tool_calls))} called" + (f", guard ran {r.guard_invoked}" if r.guard_invoked else "")
             + f"  ·  reflection rounds: {r.reflection_rounds}  ·  {r.duration_ms} ms")
    head.add_row("Agents", Text(agent, style="dim"))

    findings = Table(show_header=False, box=None, padding=(0, 1))
    for f in r.findings:
        if f.severity != Severity.INFO or f.code.value in ("CURRENCY_CONVERTED", "PREVIOUSLY_PROCESSED"):
            findings.add_row(Text(f.severity.value, style=SEV_STYLE[f.severity]), Text(f.code.value, style="bold"),
                             f.message)

    parts = [head]
    if findings.row_count:
        parts += [Text(""), findings]
    parts += [Text(""), Text(r.rationale)]
    if r.payment and r.payment.status == "success":
        parts.append(Text(f"Payment {r.payment.payment_ref}: {_usd(r.payment.amount_usd)} sent to {r.payment.vendor}",
                          style="green"))
    if r.llm_policy_disagreement:
        parts.append(Text(f"Agent recommended {r.llm_outcome.value}; policy said {r.policy_outcome.value}. "
                          "Stricter outcome applied.", style="yellow"))
    if r.error:
        parts.append(Text(f"Error: {r.error}", style="red"))
    return Panel(Group(*parts), title=f"[bold]{r.source_file}[/]",
                 subtitle=f"[bold {STYLE[r.outcome]}]{LABEL[r.outcome]}[/]", border_style=STYLE[r.outcome])


def summarize(results: list[InvoiceResult]) -> dict:
    by = {o: [r for r in results if r.outcome == o] for o in Outcome}
    total = lambda rs: round(sum(r.amount_usd or 0 for r in rs if (r.amount_usd or 0) > 0), 2)  # noqa: E731
    caught = Counter(c for r in results if r.outcome != Outcome.APPROVED for c in r.reason_codes if c != "VP_REVIEW")
    durations = [r.duration_ms for r in results if r.duration_ms is not None]
    n = len(results)
    return {
        "invoices": n,
        "approved": len(by[Outcome.APPROVED]),
        "needs_review": len(by[Outcome.NEEDS_HUMAN_REVIEW]),
        "rejected": len(by[Outcome.REJECTED]),
        "paid_usd": total(by[Outcome.APPROVED]),
        "held_usd": total(by[Outcome.NEEDS_HUMAN_REVIEW]),
        "blocked_usd": total(by[Outcome.REJECTED]),
        "straight_through_rate": round(len(by[Outcome.APPROVED]) / n, 3) if n else 0,
        "touchless_decision_rate": round((n - len(by[Outcome.NEEDS_HUMAN_REVIEW])) / n, 3) if n else 0,
        "duplicates_blocked": caught.get("DUPLICATE", 0),
        "fraud_flagged": sum(1 for r in results if "FRAUD_SIGNAL" in r.reason_codes),
        "issues_caught": dict(caught.most_common()),
        "median_seconds_per_invoice": round(median(durations) / 1000, 2) if durations else None,
        "total_seconds": round(sum(durations) / 1000, 1),
        "self_corrections": sum(1 for r in results if r.extraction_attempts > 1),
        "reflection_revisions": sum(1 for r in results if r.reflection_rounds > 1),
        "vp_reviews": sum(1 for r in results if r.vp_review),
    }


def batch_table(results: list[InvoiceResult]) -> Table:
    t = Table(title="Invoice batch results", show_lines=False, header_style="bold")
    for col, kw in (("File", {}), ("Vendor", {}), ("Amount (USD)", {"justify": "right"}), ("Outcome", {}),
                    ("Reasons", {})):
        t.add_column(col, **kw)
    for r in results:
        t.add_row(r.source_file, (r.invoice.vendor if r.invoice else None) or "—", _usd(r.amount_usd),
                  Text(r.outcome.value, style=STYLE[r.outcome]), ", ".join(r.reason_codes) or "—")
    return t


def impact_panel(s: dict) -> Panel:
    lines = [
        f"[bold]{s['invoices']}[/] invoices processed in [bold]{s['total_seconds']} s[/] "
        f"(median {s['median_seconds_per_invoice']} s each), against a 5-day manual cycle.",
        f"[green]Paid automatically:[/] {s['approved']} invoices, {_usd(s['paid_usd'])}  ·  "
        f"[yellow]held for a human:[/] {s['needs_review']}, {_usd(s['held_usd'])}  ·  "
        f"[red]blocked:[/] {s['rejected']}, {_usd(s['blocked_usd'])}",
        f"Decided without a human: [bold]{s['touchless_decision_rate']:.0%}[/] "
        f"(paid straight through: {s['straight_through_rate']:.0%}).",
        f"Caught before payment: {s['duplicates_blocked']} duplicate(s), {s['fraud_flagged']} fraud-flagged "
        f"invoice(s); top issues: " + ", ".join(f"{k} ×{v}" for k, v in list(s["issues_caught"].items())[:5]),
        f"Agent behaviour: {s['self_corrections']} extraction self-correction(s), {s['reflection_revisions']} "
        f"approval revision(s) after critique, {s['vp_reviews']} VP-level review(s).",
        "",
        "[dim]Assumptions: the case brief's baseline (about $2M/yr manual cost, 30% keying errors, 5-day cycle) has no "
        "invoice volume, so we report rates rather than projected dollars. If manual handling cost scales with "
        "touches, the share of that cost removed is roughly the 'decided without a human' rate. "
        f"Blocked amounts are exposure avoided, not savings. VP threshold ${config.VP_REVIEW_THRESHOLD_USD:,.0f}.[/]",
    ]
    return Panel("\n".join(lines), title="[bold]Business impact[/]", border_style="cyan")


def print_result(console: Console, r: InvoiceResult) -> None:
    console.print(invoice_panel(r))


def print_batch(console: Console, results: list[InvoiceResult], summary: dict) -> None:
    console.print(batch_table(results))
    console.print(impact_panel(summary))
