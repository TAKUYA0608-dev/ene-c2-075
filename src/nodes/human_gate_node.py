"""ENE-C2-075 — inner workflow step 4: human_gate (HumanApprovalGate).

Deterministic human-in-the-loop gate. It does **not** execute anything and it never switches a supplier,
changes a contract, or obtains consent — it flags the material decisions (consent/identity/compound
exceptions, and any candidate recovery routing that will drive a switching action) that require an authorized
human operator's sign-off before any action, records them + the review status into the brief, and sets
``human_review_required``. Skips (no-op) on the rejected / 0-classified safe-answer branch (no human gate
needed) after emitting a skip audit event.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.utils.audit import emit_trace_event

_URGENT_TYPES = frozenset({"consent_missing_or_expired", "customer_identity_mismatch"})


class HumanGateNode(FunctionNode):
    """Flag material decisions requiring authorized human operator approval; set human_review_required."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        report = json.loads(state.get("result") or "{}")
        if (
            state.get("error_code")
            or state.get("classified_count", 0) == 0
            or report.get("status_kind") != "switching_exception_brief"
        ):
            emit_trace_event("human_gate.skip", {"reason": state.get("error_code") or "no_brief"}, state)
            return {
                "human_review_required": False,
                "review_status": "not_required",
                "status": AgentStatus.SUCCESS.value,
            }

        material: list[dict[str, Any]] = []
        for brief in report.get("exception_briefs", []):
            # Every candidate recovery needs a human before a switching action; consent/identity/compound
            # exceptions are always flagged (consumer protection). The agent proposes; it never switches.
            if (
                brief["stalled_reason_primary"] in _URGENT_TYPES
                or brief["is_compound_exception"]
                or brief["candidate_recovery_queue"]
            ):
                material.append(
                    {
                        "case_id": brief["case_id"],
                        "stalled_reason_primary": brief["stalled_reason_primary"],
                        "is_compound_exception": brief["is_compound_exception"],
                        "reason": "Candidate recovery routing / consent/identity exception — requires authorized "
                        "operator sign-off before any consent/identity/switching action",
                    }
                )

        required = bool(material)
        review = {
            "required": required,
            "status": "pending_human_approval" if required else "not_required",
            "note": "Consent/identity reconciliation and any switching action must be confirmed by an "
            "authorized human operator. This agent produces a candidate evidence brief only.",
            "material_decisions": material,
        }
        report["human_review"] = review
        emit_trace_event(
            "human_gate.complete", {"review_required": required, "material_decision_count": len(material)}, state
        )
        return {
            "result": json.dumps(report, ensure_ascii=False),
            "human_review_required": required,
            "review_status": review["status"],
            "status": AgentStatus.SUCCESS.value,
        }
