"""ENE-C2-075 — inner workflow step 1: switch_case_classify.

Deterministic ingest + normalization of the supplied already-collected switching-case records, **cross-system
validation** (contract ↔ meter-point ↔ consent reconciliation, incl. intra-payload duplicate meter-point
double-binding detection), then per-record classification into a policy-defined 6-category exception type
(consent_missing_or_expired / customer_identity_mismatch / meter_point_mismatch / contract_conflict /
data_gap, or unclassified) against the seeded approved taxonomy, with matched drivers + evidence, a primary /
secondary (compound) reason, per-case conflict_items + missing_evidence. Sets ``classified_count``. **0 valid
cases (rejected input, non-JSON text, or all rows missing case_id) routes to the out-of-scope safe answer** —
the agent never fabricates a classification for data it did not receive. The free-text remarks are never
interpreted semantically, so prompt-like text in a supplied field cannot influence the classification.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import SwitchingExceptionService
from src.utils.audit import emit_trace_event


class SwitchCaseClassifyNode(FunctionNode):
    """Ingest + normalize + cross-validate supplied cases and classify each into a policy-defined type."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # Cases arrive already validated + provenance-resolved by pre_process (S-1): each `source` is a
        # grounded citation `src:<sha8>` or None (a forged surrogate was dropped at S-1). We do not re-run
        # provenance here — normalize trusts that single upstream resolution.
        slots = json.loads(state.get("validated_input") or state.get("user_input") or "{}")
        if not isinstance(slots, dict):
            slots = {}
        canonical = json.dumps(slots, ensure_ascii=False)
        cases = slots.get("cases") if isinstance(slots.get("cases"), list) else []

        if state.get("error_code") or not cases:
            emit_trace_event("switch_case_classify.skip", {"reason": state.get("error_code") or "no_cases"}, state)
            return {
                "validated_input": canonical,
                "classified_cases": "[]",
                "classified_count": 0,
                "error_code": state.get("error_code") or "NO_CASES",
                "status": AgentStatus.SUCCESS.value,
            }

        normalized = SwitchingExceptionService.normalize(cases)
        if not normalized:
            emit_trace_event("switch_case_classify.skip", {"reason": "all_malformed"}, state)
            return {
                "validated_input": canonical,
                "classified_cases": "[]",
                "classified_count": 0,
                "error_code": "NO_CASES",
                "status": AgentStatus.SUCCESS.value,
            }

        cross_validated = SwitchingExceptionService.cross_validate(normalized)
        classified = [SwitchingExceptionService.classify(s) for s in cross_validated]
        distribution: dict[str, int] = {}
        for c in classified:
            distribution[c["exception_type"]] = distribution.get(c["exception_type"], 0) + 1
        double_bound = sum(1 for s in cross_validated if s.get("meter_double_bound"))
        emit_trace_event(
            "switch_case_classify.complete",
            {
                "supplied": len(cases),
                "classified": len(classified),
                "type_distribution": distribution,
                "meter_double_bound": double_bound,
            },
            state,
        )
        return {
            "validated_input": canonical,
            "classified_cases": json.dumps(classified, ensure_ascii=False),
            "classified_count": len(classified),
            "status": AgentStatus.SUCCESS.value,
        }
