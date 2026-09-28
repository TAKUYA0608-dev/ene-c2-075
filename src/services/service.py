"""ENE-C2-075 — deterministic domain services (no framework imports, no LLM).

SwitchingExceptionService: normalizes an already-collected customer-switching case record (contract /
meter-point / consent identifiers + connector-supplied status/conflict signals) into a canonical signal
set, cross-validates it (contract ↔ meter-point ↔ consent reconciliation, incl. intra-payload duplicate
meter-point double-binding detection), classifies each stalled switching case into a policy-defined
6-category exception type (consent_missing_or_expired / customer_identity_mismatch / meter_point_mismatch /
contract_conflict / data_gap, or unclassified) against the seeded approved taxonomy, computes the
per-case conflict_items + missing_evidence against the required-evidence checklist, retrieves the cited
checklist / recovery-policy clauses, and composes a candidate recovery-brief entry.

Everything here is deterministic and auditable (cross-system ID reconciliation + threshold / set-membership
banding + keyed clause composition) — there is **no LLM** (no model in config/agent.yaml, no LLM dependency
in pyproject, no LLM call anywhere in src/). Records are keyed by an opaque, non-reversible ``case_id``
surrogate; the raw customer identity, contract/meter/consent references, and any free-text remarks are never
carried into the recovery brief, and the S-3 output gate re-redacts anything that leaks. Seeded taxonomy /
required-evidence checklist / recovery policy are overridable by CoE (a change-controlled engineer MR +
specialist review) without touching node logic.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# Two SEPARATE concerns — do not conflate them:
#   (1) PRIVACY (opaque_id): every caller identifier (case_id / customer_ref / contract_id / meter_point_id /
#       consent_ref) is UNCONDITIONALLY tokenized to a deterministic, non-reversible opaque surrogate so
#       consumer PII (even a bare name like ``Alice`` / ``Taro.Yamada`` / ``TaroYamada``, no spaces/symbols)
#       can never reach a citation or the recovery brief. Tokenizing is a privacy measure — it does NOT
#       assert the value is authorized/verifiable. Surrogates are one-way hashes; graph state never stores a
#       surrogate→raw rejoin map, so the opaque ID is non-linkable back to the person.
#   (2) PROVENANCE (resolve_provenance): a caller ``source`` becomes a grounded CITATION only when it is
#       resolvable against the authorized provenance registry (names a trusted switching/contract/meter/
#       consent system of record). Any other free text (a customer name, ``unknown``, a fabricated value, or
#       a caller value merely SHAPED like a surrogate ``src:1a2b3c4d``) is NOT verifiable provenance → it
#       yields NO citation → S-3 blocks the brief as CITATION_INCOMPLETE (fail-closed). "Tokenized" is never
#       sufficient for a citation.
# Tokenization is UNCONDITIONAL (no syntactic passthrough): a caller value merely *shaped* like a surrogate
# (``case:deadbeef``) is re-hashed, never trusted, so it can never forge an internal join key. Identifiers /
# provenance are resolved exactly once at S-1 (pre_process); downstream trusts that resolution verbatim.
_SAFE_TOKEN = re.compile(r"^[a-z0-9_\-]{1,48}$")

# Authorized provenance registry: the switching / contract / meter / consent systems of record a retailer
# trusts as verifiable data sources. A caller ``source`` is accepted as a grounded citation ONLY when its
# leading namespace names one of these (the "trusted context"). This is the deploying org's / CoE's registry
# — overridable without touching node logic; it is a SEMANTIC allowlist of authorized systems, not a
# syntactic character class.
AUTHORIZED_PROVENANCE_SYSTEMS = frozenset(
    {
        "switching",
        "switching_system",
        "sws",
        "switch_system",
        "switch_feed",
        "case_feed",
        "contract",
        "contract_system",
        "cis",
        "billing",
        "billing_system",
        "meter",
        "meter_point",
        "supply_point",
        "supply_point_registry",
        "mdms",
        "registry",
        "consent",
        "consent_system",
        "consent_ledger",
        "crm",
        "system_of_record",
        "sor",
        "authorized_feed",
        "ops_console",
        "workflow",
        "wfm",
    }
)


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def opaque_id(value: Any, prefix: str) -> str:
    """PRIVACY tokenize a caller identifier to a deterministic, non-reversible opaque surrogate
    ``<prefix>:<sha8>``.

    Caller identifiers are **always** tokenized — no syntactic passthrough — so a customer name (with or
    without spaces) can never survive into a citation or the brief, and a caller value merely *shaped* like a
    surrogate (``case:deadbeef``) is re-hashed rather than trusted (it can never forge an internal join key).
    Same input → same surrogate (brief / citations / summary stay joinable within one invocation). This is a
    privacy measure only; it makes no claim that the identifier is authorized, and no surrogate→raw rejoin
    map is ever kept.
    """
    return f"{prefix}:{_sha8(str(value or '').strip())}"


def resolve_provenance(value: Any) -> str | None:
    """Resolve a **raw** caller ``source`` to a grounded, privacy-tokenized CITATION — or ``None``.

    Provenance validation (separate from privacy) and the **single** resolution point (S-1 / pre_process).
    A citation is emitted **only** when the source names an authorized system of record
    (``<authorized-namespace>[:<ref>]``). Any other value — a customer name, ``unknown``, a fabricated value,
    **or a value that merely looks like a surrogate (``src:1a2b3c4d``)** — is not verifiable provenance and
    returns ``None`` so the S-3 gate blocks the brief as CITATION_INCOMPLETE (fail-closed). When authorized,
    the raw label is never used verbatim: the citation is a privacy hash (``src:<sha8>``) of the authorized
    reference. No synthetic provenance is fabricated.

    ★ Forged-surrogate defence: there is **no format-based passthrough**. A caller-supplied ``src:<hex>``
    has namespace ``src`` (not an authorized system of record), so it resolves to ``None`` — it is dropped
    here at S-1 and can never reach a citation. Because provenance is resolved exactly once (here), the
    produced ``src:<sha8>`` is the trusted citation downstream and is **never** fed back through this
    function (which would, correctly, reject it), so no forged value can imitate an internal surrogate.
    """
    text = str(value or "").strip()
    if not text:
        return None
    namespace = text.split(":", 1)[0].strip().lower()
    if namespace not in AUTHORIZED_PROVENANCE_SYSTEMS:
        return None  # unverifiable / forged-surrogate provenance → fail-closed (no citation → needs_review)
    return "src:" + _sha8(text)


# ── seeded switching-exception taxonomy: policy-defined type → human-readable description ──
EXCEPTION_TAXONOMY: dict[str, str] = {
    "consent_missing_or_expired": "The switching consent record is missing, expired, or revoked",
    "customer_identity_mismatch": "The customer identity does not reconcile across contract / meter / consent",
    "meter_point_mismatch": "The supply meter-point is mismatched or bound to more than one contract",
    "contract_conflict": "The contract status conflicts with an in-flight switch (suspended/terminated/held)",
    "data_gap": "A required switching-evidence field is missing from the case record",
    "unclassified": "No policy-defined switching-exception type matched; routed for manual review",
}

# ── seeded, approved required-evidence checklist (authorized clauses, CoE-calibratable) ──
# clause_id@version is a stable, citable reference; ``items`` are the evidence items required to resolve the
# exception (surfaced as missing_evidence when absent).
REQUIRED_EVIDENCE: dict[str, dict[str, Any]] = {
    "consent_missing_or_expired": {
        "clause_id": "RE-CONSENT",
        "version": "v2",
        "items": ["valid_consent_record", "consent_timestamp"],
    },
    "customer_identity_mismatch": {
        "clause_id": "RE-IDENTITY",
        "version": "v2",
        "items": ["reconciled_customer_identity", "identity_document_ref"],
    },
    "meter_point_mismatch": {
        "clause_id": "RE-METER",
        "version": "v2",
        "items": ["single_meter_point_binding", "supply_point_registration"],
    },
    "contract_conflict": {
        "clause_id": "RE-CONTRACT",
        "version": "v2",
        "items": ["single_active_contract", "contract_status_confirmation"],
    },
    "data_gap": {"clause_id": "RE-DATA", "version": "v2", "items": ["complete_case_record"]},
    "unclassified": {"clause_id": "RE-MANUAL", "version": "v2", "items": ["manual_review_notes"]},
}

# ── seeded, organisation-owned recovery policy (authorized clauses, CoE-calibratable) ──
# Each entry names the candidate recovery queue + priority tier + response SLA + non-decisional recovery
# suggestions for an exception type. ``recovery_queue`` is a policy-defined role queue, never a named person;
# the owner is confirmed by a human operator.
RECOVERY_POLICY: dict[str, dict[str, Any]] = {
    "consent_missing_or_expired": {
        "recovery_queue": "consent_management_team",
        "priority_tier": "tier2_urgent",
        "sla_hours": 24,
        "clause_id": "RP-CONSENT-01",
        "version": "v2",
        "description": "Missing/expired consent → consent-management team, urgent (consumer protection)",
        "suggestions": [
            "同意記録の有効性と失効日を確認し、切替日との前後関係を人手で照合する",
            "再同意取得の要否を切替オペレーションが判断する",
        ],
    },
    "customer_identity_mismatch": {
        "recovery_queue": "identity_verification_team",
        "priority_tier": "tier2_urgent",
        "sla_hours": 24,
        "clause_id": "RP-IDENTITY-01",
        "version": "v2",
        "description": "Identity mismatch across systems → identity-verification team, urgent",
        "suggestions": [
            "契約・供給地点・同意の需要家 identity 突合を人手で確認する",
            "本人確認書類の参照を要求し identity を reconcile する",
        ],
    },
    "meter_point_mismatch": {
        "recovery_queue": "supply_point_ops",
        "priority_tier": "tier1_standard",
        "sla_hours": 48,
        "clause_id": "RP-METER-01",
        "version": "v2",
        "description": "Meter-point mismatch / double-binding → supply-point operations",
        "suggestions": [
            "供給地点特定番号の重複紐付けを supply-point registry で是正する",
            "対象契約と供給地点の対応関係を人手で確認する",
        ],
    },
    "contract_conflict": {
        "recovery_queue": "contract_ops",
        "priority_tier": "tier1_standard",
        "sla_hours": 48,
        "clause_id": "RP-CONTRACT-01",
        "version": "v2",
        "description": "Contract-status conflict → contract operations",
        "suggestions": ["契約ステータス矛盾（保留/解約/重複）を人手で是正する", "重複契約の解消可否を判断する"],
    },
    "data_gap": {
        "recovery_queue": "data_quality_team",
        "priority_tier": "tier1_standard",
        "sla_hours": 72,
        "clause_id": "RP-DATA-01",
        "version": "v2",
        "description": "Missing evidence field → data-quality team",
        "suggestions": ["欠落フィールドの補完をソースシステム所管に依頼する"],
    },
    "unclassified": {
        "recovery_queue": "switching_ops",
        "priority_tier": "tier1_standard",
        "sla_hours": 72,
        "clause_id": "RP-UNCLASS-01",
        "version": "v2",
        "description": "Unclassified exception → switching operations for manual review",
        "suggestions": ["停滞原因を手動レビューで特定する"],
    },
}

# Deterministic banding / ordering.
_CONSENT_GAP_STATES = frozenset({"expired", "revoked", "missing", "withdrawn", "invalid"})
_CONTRACT_CONFLICT_STATES = frozenset({"suspended", "terminated", "conflict", "hold", "on_hold", "duplicate"})
_SEVERITY_WEIGHT = {"high": 2, "med": 1}
# Priority ordering for the brief (consumer-protection / urgent first).
_TYPE_RANK = {
    "consent_missing_or_expired": 5,
    "customer_identity_mismatch": 4,
    "meter_point_mismatch": 3,
    "contract_conflict": 2,
    "data_gap": 1,
    "unclassified": 0,
}
# Deterministic priority in which a matched type becomes the *primary* exception_type.
_TYPE_PRIORITY = (
    "consent_missing_or_expired",
    "customer_identity_mismatch",
    "meter_point_mismatch",
    "contract_conflict",
    "data_gap",
)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _enum(value: Any) -> str:
    """Coerce a caller status label to a lowercase safe token; anything unusual → ``unknown`` (never a raw
    free-text label reaches the signal set / brief)."""
    text = str(value or "").strip().lower().replace(" ", "_")
    return text if _SAFE_TOKEN.match(text) else "unknown"


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "match", "matched", "1"}:
        return True
    if text in {"false", "no", "mismatch", "unmatched", "0"}:
        return False
    return None


class SwitchingExceptionService:
    """Deterministic normalization, cross-system validation, classification, clause retrieval, and brief
    composition for stalled customer-switching cases."""

    # ── normalization ────────────────────────────────────────────────────────
    @staticmethod
    def normalize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Validate + canonicalize supplied switching-case records into a signal set. Rows without a
        ``case_id`` are dropped.

        The raw customer identity, contract/meter/consent references, and free-text remarks are intentionally
        reduced to opaque IDs / presence flags — they are never carried into the signal set that feeds the
        recovery brief. ``case_id`` is always privacy-tokenized. ``source`` was already resolved to a grounded
        citation (``src:<sha8>``) or ``None`` by pre_process (S-1), the single provenance-resolution point — a
        forged surrogate was dropped there. normalize trusts that value verbatim; it never re-resolves and
        never fabricates provenance.
        """
        out: list[dict[str, Any]] = []
        for raw in records or []:
            if not isinstance(raw, dict):
                continue
            raw_id = str(raw.get("case_id") or raw.get("id") or "").strip()
            if not raw_id:
                continue
            case_id = opaque_id(raw_id, "case")
            source = raw.get("source")  # already resolved (src:<sha8> or None) at S-1

            # meter-point token is opaque (privacy) but retained internally for intra-payload duplicate
            # (double-binding) cross-validation; it is never emitted into the brief.
            meter_raw = str(raw.get("meter_point_id") or "").strip()
            meter_token = opaque_id(meter_raw, "mp") if meter_raw else None

            contract_status = _enum(raw.get("contract_status"))
            consent_status = _enum(raw.get("consent_status"))
            meter_binding_count = _int(raw.get("meter_binding_count"), default=1)
            meter_conflict_flag = bool(raw.get("meter_conflict", False))
            identity_match = _bool_or_none(raw.get("identity_match"))
            missing_fields = sorted(
                {
                    str(f).strip().lower()
                    for f in (raw.get("missing_fields") or [])
                    if _SAFE_TOKEN.match(str(f).strip().lower())
                }
            )

            consent_gap = consent_status in _CONSENT_GAP_STATES
            contract_conflict = contract_status in _CONTRACT_CONFLICT_STATES
            identity_gap = identity_match is False
            meter_conflict = meter_binding_count > 1 or meter_conflict_flag
            data_gap = bool(missing_fields)

            out.append(
                {
                    "case_id": case_id,
                    "meter_token": meter_token,
                    "contract_status": contract_status,
                    "consent_status": consent_status,
                    "meter_binding_count": meter_binding_count,
                    "identity_match": identity_match,
                    "missing_fields": missing_fields,
                    "consent_gap": consent_gap,
                    "contract_conflict": contract_conflict,
                    "identity_gap": identity_gap,
                    "meter_conflict": meter_conflict,
                    "data_gap": data_gap,
                    "source": source,
                }
            )
        return out

    # ── cross-system validation ────────────────────────────────────────────────
    @staticmethod
    def cross_validate(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deterministic cross-system reconciliation across the payload: a meter-point (opaque token) bound
        to more than one case is a double-binding → set ``meter_conflict`` on every case sharing it. This is
        a real cross-reference (not a per-row flag), and it is deterministic — no free text is interpreted.
        Returns a new list with ``meter_conflict`` / ``meter_double_bound`` updated."""
        counts: dict[str, int] = {}
        for s in signals:
            tok = s.get("meter_token")
            if tok:
                counts[tok] = counts.get(tok, 0) + 1
        out: list[dict[str, Any]] = []
        for s in signals:
            tok = s.get("meter_token")
            double_bound = bool(tok and counts.get(tok, 0) > 1)
            updated = dict(s)
            updated["meter_conflict"] = s["meter_conflict"] or double_bound
            updated["meter_double_bound"] = double_bound
            out.append(updated)
        return out

    # ── classification ───────────────────────────────────────────────────────
    @staticmethod
    def classify(signals: dict[str, Any]) -> dict[str, Any]:
        """Classify one cross-validated case into a policy-defined type + matched drivers (with evidence),
        a primary / secondary (compound) reason, per-case conflict_items and missing_evidence.

        Deterministic set-membership / threshold matching only — the free-text remarks are never interpreted
        semantically, so prompt-like text in a supplied field can never influence the classification.
        """
        matched: list[dict[str, str]] = []
        conflict_items: list[dict[str, str]] = []

        if signals["consent_gap"]:
            matched.append(
                {
                    "exception_type": "consent_missing_or_expired",
                    "severity": "high",
                    "evidence": f"consent_status={signals['consent_status']}",
                }
            )
            conflict_items.append({"code": "consent_gap", "detail": f"consent_status={signals['consent_status']}"})
        if signals["identity_gap"]:
            matched.append(
                {"exception_type": "customer_identity_mismatch", "severity": "high", "evidence": "identity_match=false"}
            )
            conflict_items.append({"code": "identity_mismatch", "detail": "identity does not reconcile"})
        if signals["meter_conflict"]:
            double = signals["meter_binding_count"] > 1 or signals.get("meter_double_bound")
            sev = "high" if double else "med"
            detail = f"meter_binding_count={signals['meter_binding_count']}" + (
                " (double-bound across cases)" if signals.get("meter_double_bound") else ""
            )
            matched.append({"exception_type": "meter_point_mismatch", "severity": sev, "evidence": detail})
            conflict_items.append({"code": "meter_double_binding", "detail": detail})
        if signals["contract_conflict"]:
            matched.append(
                {
                    "exception_type": "contract_conflict",
                    "severity": "med",
                    "evidence": f"contract_status={signals['contract_status']}",
                }
            )
            conflict_items.append(
                {"code": "contract_status_conflict", "detail": f"contract_status={signals['contract_status']}"}
            )
        if signals["data_gap"]:
            matched.append(
                {
                    "exception_type": "data_gap",
                    "severity": "med",
                    "evidence": f"missing_fields={signals['missing_fields']}",
                }
            )
            conflict_items.append({"code": "data_gap", "detail": f"missing_fields={signals['missing_fields']}"})

        matched_types = [m["exception_type"] for m in matched]
        primary = next((t for t in _TYPE_PRIORITY if t in matched_types), "unclassified")
        secondary = next((t for t in _TYPE_PRIORITY if t in matched_types and t != primary), None)
        is_compound = len(set(matched_types)) > 1
        severity_score = sum(_SEVERITY_WEIGHT[m["severity"]] for m in matched)

        # missing_evidence: the required-evidence checklist for every matched type, plus the explicit
        # caller-declared missing fields (deterministic union, ordered).
        evidence_items: list[str] = []
        for t in matched_types or ["unclassified"]:
            for item in REQUIRED_EVIDENCE.get(t, REQUIRED_EVIDENCE["unclassified"])["items"]:
                if item not in evidence_items:
                    evidence_items.append(item)
        for f in signals["missing_fields"]:
            token = f"missing_field:{f}"
            if token not in evidence_items:
                evidence_items.append(token)

        return {
            "case_id": signals["case_id"],
            "exception_type": primary,
            "secondary_type": secondary,
            "is_compound_exception": is_compound,
            "matched_drivers": matched,
            "matched_types": sorted(set(matched_types)),
            "conflict_items": conflict_items,
            "missing_evidence": evidence_items,
            "severity_score": severity_score,
            "priority_rank": _TYPE_RANK[primary],
            "contract_status": signals["contract_status"],
            "consent_status": signals["consent_status"],
            "source": signals["source"],
        }

    # ── clause retrieval ───────────────────────────────────────────────────────
    @staticmethod
    def retrieve_references(classified: dict[str, Any]) -> dict[str, list[str]]:
        """Deterministically retrieve the cited required-evidence-checklist + recovery-policy clauses for one
        classified case. Clause refs are ``<clause_id>@<version>`` from the seeded authorized policy."""
        types = classified["matched_types"] or [classified["exception_type"]]
        evidence_refs: list[str] = []
        for t in types:
            entry = REQUIRED_EVIDENCE.get(t, REQUIRED_EVIDENCE["unclassified"])
            ref = f"{entry['clause_id']}@{entry['version']}"
            if ref not in evidence_refs:
                evidence_refs.append(ref)

        policy = RECOVERY_POLICY.get(classified["exception_type"], RECOVERY_POLICY["unclassified"])
        policy_refs = [f"{policy['clause_id']}@{policy['version']}"]
        return {"evidence_refs": evidence_refs, "policy_refs": policy_refs}

    # ── brief composition ──────────────────────────────────────────────────────
    @staticmethod
    def compose_brief(classified: dict[str, Any], refs: dict[str, list[str]]) -> dict[str, Any]:
        """Compose the per-case candidate recovery-brief entry (needs-review, cited).

        The candidate recovery queue is a policy-defined role queue, never a named person, and the entry is a
        candidate only — the final consent/identity/switching decision defers to a human operator. The
        recovery suggestions are non-decisional evidence follow-up options, not switching actions.
        """
        policy = RECOVERY_POLICY.get(classified["exception_type"], RECOVERY_POLICY["unclassified"])
        candidate_recovery = [
            {
                "recovery_queue": policy["recovery_queue"],
                "priority_tier": policy["priority_tier"],
                "sla_hours": policy["sla_hours"],
                "reason": policy["description"],
            }
        ]
        return {
            "case_id": classified["case_id"],
            "stalled_reason_primary": classified["exception_type"],
            "stalled_reason_secondary": classified["secondary_type"],
            "is_compound_exception": classified["is_compound_exception"],
            "exception_description": EXCEPTION_TAXONOMY.get(classified["exception_type"], ""),
            "matched_drivers": classified["matched_drivers"],
            "conflict_items": classified["conflict_items"],
            "missing_evidence": classified["missing_evidence"],
            "severity_score": classified["severity_score"],
            "priority_rank": classified["priority_rank"],
            "candidate_recovery_queue": candidate_recovery,
            "recovery_suggestions": list(policy["suggestions"]),
            "cited_evidence_refs": refs["evidence_refs"],
            "cited_policy_refs": refs["policy_refs"],
            "status_kind": "needs_review",
            "note": "Candidate evidence brief only — the final consent/identity/switching decision is an "
            "authorized operator's; this agent organizes evidence and does not switch a supplier, "
            "change a contract, obtain consent, or contact a customer.",
            "citation": classified["source"],
        }

    @staticmethod
    def exception_summary(briefs: list[dict[str, Any]]) -> dict[str, Any]:
        """Portfolio-level rollup: case count, stalled-reason distribution, cases needing urgent review."""
        distribution: dict[str, int] = {}
        for b in briefs:
            distribution[b["stalled_reason_primary"]] = distribution.get(b["stalled_reason_primary"], 0) + 1
        urgent = [
            b["case_id"]
            for b in briefs
            if b["stalled_reason_primary"] in ("consent_missing_or_expired", "customer_identity_mismatch")
            or b["is_compound_exception"]
        ]
        return {
            "total_cases": len(briefs),
            "stalled_reason_distribution": distribution,
            "cases_needing_urgent_review": urgent,
        }
