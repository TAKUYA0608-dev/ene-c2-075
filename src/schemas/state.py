"""ENE-C2-075 — Agent state (Electricity-Retail Customer Switching Exception Evidence Agent, Cat 2).

ADR-005: State is a flat TypedDict — never a validation/BaseModel instance. Complex fields are stored
as JSON strings (``NotRequired[str]`` + ``# JSON:``); nodes ``json.dumps`` on write / ``json.loads`` on read.

Read-only / advisory: the agent ingests already-collected customer-switching case records (pseudonymous
contract / meter-point / consent identifiers + free-text remarks), cross-validates them, classifies each
stalled switching case against the approved switching-exception taxonomy + required-evidence checklist, and
produces a **SwitchingExceptionBrief** deliverable — it never executes a supplier switch, changes a
contract, obtains or represents consent, contacts a customer, or decides switch eligibility. The final
consent / identity / switching decision is always an authorized human operator's, and every brief is
candidate / needs-review only.

All agent-specific fields are NotRequired (populated progressively; absent at empty-start invoke).
"""

from __future__ import annotations


from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Agent state for the customer-switching exception classification + recovery-brief workflow."""

    # ── pre_process (EvidenceRetrieve + SensitiveDataMinimise; S-1 + S-2 pre-workflow) ──
    validated_input: str  # JSON: {cases[], scope, period} (consent/identity PII minimised)
    input_format: str  # "json" | "text" | "empty" | "rejected"
    enriched_context: str  # JSON: {source, channel} (read-only caller context)

    # ── inner workflow (switch_case_classify → evidence_reference_retrieve → recovery_brief_compose → human_gate) ─
    classified_cases: str  # JSON: [{case_id, exception_type, missing_evidence[], conflict_items[], source}]
    classified_count: int  # switching cases classified (0 → out-of-scope safe answer)
    evidence_references: str  # JSON: {case_id: {evidence_refs[], policy_refs[]}}
    result: str  # JSON: assembled SwitchingExceptionBrief (incl. human_review)
    human_review_required: bool  # True once the HumanApprovalGate flags material decisions
    review_status: str  # "pending_human_approval" | "not_required"

    # ── post_process (BriefCompose — S-3 gate + S-4 audit) ────────────────────
    formatted_output: str  # JSON: final response envelope (brief + disclaimer)
    disclaimer: str  # mandatory DRAFT / advisory-only disclaimer
    audit_logged: bool  # True once the terminal audit event is emitted

    # ── degraded-path signalling (SUCCESS + error_code, never status=ERROR) ───
    # INPUT_REJECTED | INJECTION_REJECTED | INPUT_TOO_LONG | NO_CASES | CITATION_INCOMPLETE
    error_code: str
    error_message: str  # operator-facing detail
