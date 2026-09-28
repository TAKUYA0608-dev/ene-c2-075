# Template Design Specification — ENE-C2-075

Electricity-Retail Customer Switching Exception Evidence Agent (Cat 2, GraphNode-in-main).

## Position in AgentCore Architecture

- **Agent Class**: `ElectricityRetailCustomerSwitchingExceptionEvidenceAgent` (module-level alias of `Graph`)
- **L1 Base**: AgentBaseGraph (L1 direct — Cat 2 GraphNode-in-main; **not** AutonomousBaseGraph). The
  `DocGenerationAgent` L2 pattern is a design reference only; the workflow is implemented directly on
  AgentBaseGraph (2026-05-18 L2-deprecation ruling).
- **Category**: Cat 2 — orchestrates a fixed multi-step workflow to produce one job-to-be-done deliverable
  (a SwitchingExceptionBrief for an already-stalled customer-switching case).
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible); complex fields are JSON strings (ADR-005)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only — no `config` param)
  - Graph: composition (`register_nodes()` for node substitution; domain complexity behind a `GraphNode`)
- **LLM**: none. The template is **fully deterministic** (cross-system ID reconciliation + threshold /
  set-membership classification + keyed clause composition against a seeded approved switching-exception
  taxonomy / required-evidence checklist / recovery policy). There is no model in `config/agent.yaml`, no LLM
  dependency in `pyproject.toml`, and no LLM call anywhere in `src/`. "pre-LLM" in the S-2 discussion below
  therefore means "before any downstream node reads the minimised free-text remarks".

## Architecture Overview

### Node Configuration (outer 5-slot backbone)

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | schema/session/trust setup | user_input | caller_trust_level, session_id | InitializeNode (default) |
| pre_process | `EvidenceRetrieve` + `SensitiveDataMinimise` — S-1 normalisation (NFKC, control-char strip, size cap) + S-2 pre-workflow sensitive-data minimisation. **injection/oversize → degraded `SUCCESS + error_code`, offending body discarded (never `status=ERROR`)**. Field-level input hygiene (credential/My-Number/email/phone redaction); **customer-identity PII display fields (name/address/contact) dropped**; identifiers (`case_id`/`customer_ref`/`contract_id`/`meter_point_id`/`consent_ref`) **UNCONDITIONALLY tokenized to opaque, non-reversible surrogates** (a bare name is opaque like any value, no surrogate→raw rejoin map kept); **provenance `source` resolved to a citation ONLY if it names an authorized switching/contract/meter/consent system of record (privacy-tokenized `src:<sha8>`), else dropped to `None`** — S-3 then blocks; `scope`/`period`/`remarks` hygiened | user_input | validated_input, input_format, enriched_context, (error_code) | PreProcessNode (FunctionNode) |
| main | `SwitchingExceptionEvidenceWorkflowGraphNode` — wraps inner `SwitchingExceptionEvidenceWorkflow` (composition criterion #9) | validated_input | result, classified_count, human_review_required, (error_code), status | GraphNode (subgraph) |
| post_process | `BriefCompose` — S-3 output gate: **fail-closed per-case citation completeness** (ungrounded brief → `needs_review` degrade, brief body withheld, `error_code=CITATION_INCOMPLETE`) + customer-name/company/phone/email/credential/My-Number re-redaction + DRAFT disclaimer, S-4 no-persist audit | result | formatted_output, disclaimer, audit_logged, (error_code) | PostProcessNode (FunctionNode) |
| finalize | build response envelope | formatted_output | output, status | FinalizeNode (default) |

### Inner workflow (`src/graph/domain_workflow_graph.py` — BaseGraph, linear + per-node skip guard)

```
START → switch_case_classify → evidence_reference_retrieve → recovery_brief_compose → human_gate → END
```

| Inner Node | Responsibility | Skip guard |
|------|---------------|-----------|
| switch_case_classify | Deterministic ingest + normalize of the supplied switching-case records; **cross-system validation** (contract ↔ meter-point ID ↔ consent reconciliation, incl. intra-payload duplicate meter-point double-binding detection); derive stalled-switch signals (consent gap, meter-point mismatch, contract conflict, customer-identity mismatch, data gap); classify each into a policy-defined 6-category taxonomy (consent_missing_or_expired / customer_identity_mismatch / meter_point_mismatch / contract_conflict / data_gap / unclassified) with matched drivers + evidence, a primary / secondary (compound) reason, and per-case `conflict_items` + `missing_evidence`; set `classified_count`. **0 valid cases → `error_code=NO_CASES` → out-of-scope safe answer**. Free-text remarks are never interpreted semantically | — (first node; emits `.skip` on rejected/no-case input) |
| evidence_reference_retrieve | Deterministic retrieval of the cited required-evidence-checklist + recovery-policy clauses (`<clause_id>@<version>`) for each classified case, grounding the recovery brief in authorized clauses | no-op `return {}` (after `.skip` emit) on `error_code` / `classified_count == 0` |
| recovery_brief_compose | Compose the SwitchingExceptionBrief deliverable: per case, the stalled-reason primary/secondary, `is_compound_exception`, `conflict_items`, `missing_evidence`, non-decisional `recovery_suggestions` + a candidate recovery queue (priority tier + SLA), cited evidence/policy clauses, a needs-review mark, and the source citation; ordered by priority (consent/identity/urgent first). On 0-case/rejected → out-of-scope safe answer | emits safe answer on `error_code` / no cases |
| human_gate | Deterministic **HumanApprovalGate**: mark `human_review_required=True` + `review_status="pending_human_approval"`, record material decisions (consent/identity/compound exceptions and any candidate recovery routing requiring an authorized operator's sign-off before a switching action) into the brief. A switch is **never** executed by the agent | no-op `return {}` (after `.skip` emit) on `error_code` / `classified_count == 0` (safe answer needs no human gate) |

`SwitchingExceptionEvidenceWorkflowGraphNode.get_subgraph()` caches the compiled inner workflow on the
**class attribute** (`SwitchingExceptionEvidenceWorkflowGraphNode._subgraph`, not `self` — avoids mutable
node-instance state per §9; built once; `BaseGraph.invoke()` `_ensure_compiled` is idempotent).
`extract_input()` passes `validated_input` into the inner graph; `merge_output()` surfaces `result /
classified_count / human_review_required / error_code / status` — with **`error_code` OUTER-first**
(`state.get("error_code") or sub_result.get("error_code")`) so a pre-stage rejection survives to the
terminal S-4 audit (the inner workflow runs on the discarded body and would otherwise overwrite it with
`NO_CASES`).

## Security Model (S-1 … S-5)

- **S-1 (input normalisation + field hygiene)**: NFKC + control-char strip + size cap; every string written
  into `validated_input` is passed through credential/My-Number/email/phone redaction; identifiers are
  tokenized to opaque surrogates; provenance resolved exactly once here.
- **S-2 (pre-workflow sensitive-data minimisation, consent/identity data)**: customer-identity PII display
  fields (name/address/contact/email/phone/My-Number) are **dropped entirely — not masked** — before the
  workflow runs, and every caller identifier (`case_id`/`customer_ref`/`contract_id`/`meter_point_id`/
  `consent_ref`) is tokenized to an opaque, non-reversible surrogate with **no surrogate→raw rejoin map in
  graph state**; the S-4 audit references only minimised counts. **Injection containment is pre-workflow**:
  prompt-injection markers or oversize input degrade to a safe out-of-scope answer *without any semantic
  execution of the offending text*.
  - **Degraded contract (SDK 1.0.0)**: an S-2 rejection is surfaced as **`status=SUCCESS` + `error_code`**
    (`INJECTION_REJECTED` / `INPUT_TOO_LONG` / `INPUT_REJECTED`) with the offending body discarded — it is
    **never `status=ERROR`** (which would short-circuit `route()` straight to `finalize`, skipping
    `post_process` and thus the disclaimer / S-3 redaction / S-4 audit). `post_process` therefore always
    runs and always delivers the out-of-scope safe answer + disclaimer + audit. `_extra_security_gate_input`
    MUST NOT raise and MUST `return dict(state)`; `execute()` re-checks the same conditions because the
    local stub framework does not invoke the `@final` hook.
- **S-3 (output gate, fail-closed)**: enforce per-case citation completeness — a grounded
  SwitchingExceptionBrief in which any case lacks a verifiable `source` citation is **never presented**;
  it degrades to `needs_review` with the brief body withheld (`error_code=CITATION_INCOMPLETE`, still
  SUCCESS). Re-redact any leaked secret/contact/name pattern (defense-in-depth). Append the mandatory DRAFT
  advisory disclaimer. `_extra_security_gate_output` receives the `execute()` result delta and MAY raise to
  block an output missing the disclaimer. **Injection block is asserted at S-3/output** (in addition to the
  pre-workflow degrade) so prompt-like text can never reach the delivered brief.
- **S-4 (audit, no-persist)**: every node `execute()` path — including every skip/0-count/degraded branch —
  emits a count-only domain trace event via `src.utils.audit.emit_trace_event`; payloads carry counts /
  type distribution / policy-type keys / error codes only (no customer name, free-text remarks, consent
  record, or un-masked contract/meter identifier). The raw record is not retained.
- **S-5 (rate limit / abuse)**: enforced at the platform entry point; the agent is read-only and performs
  no external write.

## Read-only / non-execution boundary

The agent **never** executes a supplier switch, changes a contract, obtains or represents consent, contacts
a customer, or decides switch eligibility. All output is **candidate / needs-review**, and the final
consent / identity / switching decision is always an authorized human operator's, gated by the
HumanApprovalGate (the candidate recovery queue is a policy-defined role queue, never a named person).
A mistaken stall judgment can affect a customer's right to switch (consumer protection), so ambiguity and
material decisions are always routed to a human.

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework/` and `shared/` only (no `agents/base/` required)

## Open Items (Stage ③ implementation plan)

The design MR ships `docs/02` + `src/schemas/state.py` only. The Stage ③ implementation MR adds: the six
node implementations (pre_process, the four inner nodes, post_process), the inner/outer graph wiring
(`get_subgraph` caching + `merge_output` outer-first error_code), the deterministic
`SwitchingExceptionService` (seeded switching-exception taxonomy + required-evidence checklist + recovery
policy + provenance/opaque-id helpers), `src/utils/audit.py` (S-4 shim), the
`ElectricityRetailCustomerSwitchingExceptionEvidenceAgent = Graph` registry alias, and the unit /
integration / real-invoke tests (including the forged-surrogate and customer-identity-PII regressions).
Seeded taxonomy / checklist / recovery policy are CoE-calibratable via a change-controlled engineer MR +
specialist review — they are not runtime-editable operational actions.
