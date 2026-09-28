"""ENE-C2-075 — inner workflow step 3: recovery_brief_compose.

Composes the **SwitchingExceptionBrief** deliverable: an exception summary, and a per-case recovery entry
(policy-defined stalled-reason primary/secondary, compound flag, conflict_items, missing_evidence,
non-decisional recovery_suggestions + a candidate recovery queue with priority tier + SLA, cited evidence /
policy clauses, needs-review mark), each cited to its source record. Cases are ordered by priority
(consent / identity / urgent first). The brief is candidate / advisory only — it never executes a switch,
changes a contract, obtains consent, or contacts a customer. On the 0-classified / rejected branch it emits
the out-of-scope safe answer.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import SwitchingExceptionService
from src.utils.audit import emit_trace_event

_OUT_OF_SCOPE = (
    "分類可能な切替例外ケースが入力に見つかりませんでした。cases 配列に case_id と contract_status・"
    "consent_status・meter_point_id・meter_binding_count・identity_match・missing_fields 等を含む JSON を"
    "ご指定いただくか、対象範囲・期間を明確にしてください。"
)


class RecoveryBriefComposeNode(FunctionNode):
    """Compose the SwitchingExceptionBrief deliverable with citations (or safe answer on 0-classified)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        classified = json.loads(state.get("classified_cases") or "[]")
        if state.get("error_code") or not classified:
            emit_trace_event(
                "recovery_brief_compose.safe", {"reason": state.get("error_code") or "no_classified"}, state
            )
            report: dict[str, Any] = {
                "status_kind": "out_of_scope",
                "message": _OUT_OF_SCOPE,
                "exception_summary": {},
                "exception_briefs": [],
                "citations": [],
            }
            return {"result": json.dumps(report, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}

        references = json.loads(state.get("evidence_references") or "{}")
        briefs: list[dict[str, Any]] = []
        citations: list[dict[str, str]] = []
        for c in classified:
            refs = references.get(c["case_id"], {"evidence_refs": [], "policy_refs": []})
            briefs.append(SwitchingExceptionService.compose_brief(c, refs))
            citations.append({"case_id": c["case_id"], "source": c["source"]})
        briefs.sort(key=lambda b: (-b["priority_rank"], -b["severity_score"], b["case_id"]))

        summary = SwitchingExceptionService.exception_summary(briefs)
        report = {
            "status_kind": "switching_exception_brief",
            "scope": self._scope(state),
            "exception_summary": summary,
            "exception_briefs": briefs,
            "citations": citations,
        }
        emit_trace_event(
            "recovery_brief_compose.complete",
            {
                "case_count": len(briefs),
                "queue_count": sum(len(b["candidate_recovery_queue"]) for b in briefs),
                "citation_count": len(citations),
            },
            state,
        )
        return {"result": json.dumps(report, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}

    @staticmethod
    def _scope(state: dict[str, Any]) -> dict[str, Any]:
        slots = json.loads(state.get("validated_input") or "{}")
        return {"scope": slots.get("scope"), "period": slots.get("period")}
