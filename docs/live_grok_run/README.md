# Live Grok run: sample batch, 2 October 2026

Unedited output from running all 20 sample invoices against live Grok (`grok-4.7`) on a fresh database:

```bash
python main.py --provider grok --reset-db --db-path output/grok/inventory.db --output-dir output/grok --invoice_dir=data/invoices
```

**Result:** 19 of 20 invoices matched `tests/fixtures/expected_outcomes.yaml`. 7 were paid ($30,780), 4 held for
review ($28,890) and 9 rejected ($204,008.60). The run had no errors and took 714 s (median 34 s per invoice). The
one difference is `invoice_1012.pdf`: policy would approve it, but Grok held it for review, citing the vendor rename and a
total $25 under the VP threshold. See the main README for the analysis.

| File | What it shows |
|---|---|
| [`console.log`](console.log) | What a stakeholder sees: one panel per invoice with Grok's rationale, then the batch table and business-impact summary |
| [`batch_summary.json`](batch_summary.json) | Every `InvoiceResult` (extracted invoice, findings, reason codes, policy vs agent outcome, rationale, payment) plus summary metrics |
| [`audit.jsonl`](audit.jsonl) | One event per graph node per invoice: latency, tool calls Grok requested, findings, policy decision, critique verdicts and issues, LLM–policy disagreements |
| [`review_queue.jsonl`](review_queue.jsonl) | The 4 invoices waiting for a person, with reasons and rationale |

Things worth looking for:
- `invoice_1012.pdf` in `audit.jsonl`: the `finalize` event shows `"llm_policy_disagreement": true` (policy
  APPROVED, Grok NEEDS_HUMAN_REVIEW).
- `invoice_1007.csv` in `audit.jsonl`: two `critique` events. The first is `revise` (the draft never compared
  $15,525 with the $10K threshold), the second is `accept`.
- `invoice_1004_revised.json` in `batch_summary.json`: Grok's rationale works out the +$4,050 difference and why
  paying the full revised total would pay the original $1,890 twice.
- `validator_agent` events: Grok requested all 7 checks itself on every invoice; `guard` events show `guard_ran: []`.
- `invoice_1008.txt` and `invoice_1016.json`, the two invoices with items missing from inventory: Grok also called
  the optional `lookup_inventory_item` tool to investigate the unknown names before summarising.

The mock provider produces different rationales (templated) and, for 1012, a different outcome. Re-running against
live Grok can also vary slightly in wording.
