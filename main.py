"""Acme Corp invoice processing pipeline.

    python main.py --invoice_path=data/invoices/invoice_1001.txt
    python main.py --invoice_dir=data/invoices
    python main.py --invoice_dir=data/invoices --provider mock --reset-db
    python main.py --ui                      (optional web UI; pip install -r requirements-ui.txt)
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import sys

from dotenv import load_dotenv
from rich.console import Console

from invoice_agents import audit, config, report
from invoice_agents.graph import build_deps, build_graph, invoice_files, run_invoice
from invoice_agents.llm import LLMConfigError


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Process invoices: ingest, validate, approve, pay.")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--invoice_path", help="a single invoice file (.txt, .pdf, .json, .csv, .xml)")
    target.add_argument("--invoice_dir", help="process every invoice in a directory, in filename order")
    target.add_argument("--ui", action="store_true",
                        help="open the optional local web UI (drop files, watch each agent stage)")
    p.add_argument("--provider", choices=["grok", "mock"],
                   help="LLM backend (default: $LLM_PROVIDER or grok)")
    p.add_argument("--db-path", default=str(config.DEFAULT_DB_PATH), help="SQLite inventory + ledger file")
    p.add_argument("--output-dir", default=str(config.DEFAULT_OUTPUT_DIR), help="audit log and result files")
    p.add_argument("--reset-db", action="store_true",
                   help="reseed inventory and clear the payment ledger before running")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON instead of panels")
    p.add_argument("--port", type=int, default=8765, help="--ui: port on 127.0.0.1 (default 8765)")
    p.add_argument("--no-browser", action="store_true", help="--ui: don't open a browser tab automatically")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    console = Console(stderr=args.json)
    if args.ui and importlib.util.find_spec("flask") is None:
        console.print("[red]The web UI needs Flask: pip install -r requirements-ui.txt[/red]")
        return 2
    try:
        deps = build_deps(args.provider, db_path=args.db_path, output_dir=args.output_dir, reset_db=args.reset_db)
    except LLMConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    if args.ui:
        from invoice_agents.ui.server import serve

        serve(deps, port=args.port, open_browser=not args.no_browser)
        return 0

    console.print(f"[dim]LLM: {deps.provider} ({deps.model})  ·  DB: {deps.db_path}  ·  "
                  f"audit: {deps.output_dir / 'audit.jsonl'}[/dim]")
    graph = build_graph(deps)
    paths = invoice_files(args.invoice_dir) if args.invoice_dir else [args.invoice_path]
    results = []
    for path in paths:
        # In --json mode keep stdout pure JSON: the case brief's mock_payment stub prints, so route it to stderr.
        with contextlib.redirect_stdout(sys.stderr) if args.json else contextlib.nullcontext():
            result = run_invoice(path, deps, graph)
        results.append(result)
        if not args.json:
            report.print_result(console, result)

    summary = report.summarize(results)
    audit.write_json(deps.output_dir / "batch_summary.json",
                     {"summary": summary, "results": [r.model_dump(mode="json") for r in results]})
    if args.json:
        payload = results[0].model_dump(mode="json") if args.invoice_path else \
            {"summary": summary, "results": [r.model_dump(mode="json") for r in results]}
        print(json.dumps(payload, indent=2))
    elif args.invoice_dir:
        report.print_batch(console, results, summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
