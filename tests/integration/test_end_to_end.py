# ENE-C2-075 — Integration: full outer Graph().invoke() across a multi-case switching portfolio

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph

_SUCCESS = AgentStatus.SUCCESS.value


def _invoke(user_input: str):
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
    return Graph().invoke(user_input, ctx=ctx)


def test_multi_case_portfolio_prioritised_and_grounded():
    payload = {
        "scope": "kanto-batch",
        "cases": [
            {"case_id": "c1", "contract_status": "active", "consent_status": "valid",
             "meter_binding_count": 1, "missing_fields": ["supply_point_registration"],
             "source": "meter:c1"},                                              # data_gap
            {"case_id": "c2", "contract_status": "suspended", "consent_status": "expired",
             "meter_binding_count": 2, "identity_match": False, "source": "switching:c2"},  # compound (urgent)
            {"case_id": "c3", "contract_status": "conflict", "consent_status": "valid",
             "source": "contract:c3"},                                           # contract_conflict
        ],
    }
    out = _invoke(json.dumps(payload))
    assert out["status"] == _SUCCESS
    env = json.loads(out["output"])
    assert env["status_kind"] == "switching_exception_brief"
    assert len(env["exception_briefs"]) == 3 and len(env["citations"]) == 3
    # consent/identity compound exception is ranked first
    assert env["exception_briefs"][0]["stalled_reason_primary"] == "consent_missing_or_expired"
    # summary rolls up the distribution and the urgent-review list
    assert env["exception_summary"]["total_cases"] == 3
    assert env["exception_briefs"][0]["case_id"] in env["exception_summary"]["cases_needing_urgent_review"]
    assert env["human_review"]["required"] is True
    assert "DRAFT" in env["disclaimer"]


def test_mixed_cited_and_uncited_blocks_whole_brief():
    """Per-case citation completeness: one uncited case fails-closed the whole grounded brief."""
    payload = {"cases": [
        {"case_id": "c1", "consent_status": "expired", "source": "consent:c1"},   # cited
        {"case_id": "c2", "consent_status": "expired"},                            # uncited
    ]}
    out = _invoke(json.dumps(payload))
    env = json.loads(out["output"])
    assert env["status_kind"] == "needs_review"
    assert env["exception_briefs"] == [] and env["citations"] == []


def test_forged_case_id_surrogate_rehashed():
    # ★ a caller value SHAPED like an internal surrogate (case:deadbeef) is re-hashed at S-1 (no syntactic
    # passthrough), so it can never forge an internal join key / reference another case.
    payload = {"cases": [
        {"case_id": "case:deadbeef", "consent_status": "expired", "source": "consent:c1"},
    ]}
    out = _invoke(json.dumps(payload))
    env = json.loads(out["output"])
    assert env["status_kind"] == "switching_exception_brief"
    tok = env["exception_briefs"][0]["case_id"]
    assert tok.startswith("case:") and tok != "case:deadbeef"   # re-hashed, not passthrough
    assert "case:deadbeef" not in out["output"]


def test_empty_object_is_out_of_scope():
    out = _invoke(json.dumps({"cases": []}))
    env = json.loads(out["output"])
    assert env["status_kind"] == "out_of_scope" and env["citations"] == []
