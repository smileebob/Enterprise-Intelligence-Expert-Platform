"""招商评估输入装配：object_ref（企业）→ 六维打分器输入。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from domains.investment.models import InvEnterprise, InvProfile


def build_scorer_inputs(db: Session, object_ref: dict[str, Any]) -> dict[str, Any]:
    ent_id = str(object_ref.get("enterprise", "") or (object_ref.get("products") or [""])[0])
    enterprise = db.get(InvEnterprise, ent_id) if ent_id else None
    profile = db.get(InvProfile, f"PROF-{ent_id}") if ent_id else None
    return {
        "ent_id": ent_id,
        "six_dim": (profile.six_dim if profile else {}) or {},
        "data_completeness": float(profile.data_completeness) if profile and profile.data_completeness is not None else 0.0,
        "tech_direction": (profile.tech_direction if profile else {}) or {},
        "funding_stage": profile.funding_stage if profile else "",
        "track": enterprise.track if enterprise else "",
        "evidence_refs": [ent_id] if ent_id else [],
        "n_cases": 1 if profile else 0,
        "avg_conf": 0.8 if profile else 0.0,
    }
