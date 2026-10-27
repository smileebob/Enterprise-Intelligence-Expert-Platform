"""Phase 27 · 政务政策闭环：导入幂等、审核发布、版本适用、检索与差异。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.gov_office.services as gov_services
from core.admin.models import DomInstance
from core.domains import DomainRegistry
from domains.gov_office.models import GovPolicyDocument

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "政务智能体" / "samples" / "public"
NEW_SAMPLE = json.loads((SAMPLE_DIR / "GOV-REG-711-2019.json").read_text(encoding="utf-8"))
OLD_SAMPLE = json.loads((SAMPLE_DIR / "GOV-REG-492-2007.json").read_text(encoding="utf-8"))
DOC_ID = NEW_SAMPLE["document"]["doc_id"]
NEW_LABEL = NEW_SAMPLE["version"]["version_label"]
OLD_LABEL = OLD_SAMPLE["version"]["version_label"]


def _import(client, headers, sample, key):
    return client.post(
        "/api/v1/gov/policies/import",
        json=sample,
        headers={**headers, "Idempotency-Key": key},
    )


def _approve_and_publish(client, headers, sample):
    doc_id = sample["document"]["doc_id"]
    label = sample["version"]["version_label"]
    slot = "new" if sample is NEW_SAMPLE else "old"
    reviewed = client.post(
        f"/api/v1/gov/policies/{doc_id}/versions/{label}/review",
        json={"action": "approve", "note": "公开样本人工复核通过"},
        headers={**headers, "Idempotency-Key": f"review-{slot}"},
    )
    assert reviewed.status_code == 200, reviewed.text
    published = client.post(
        f"/api/v1/gov/policies/{doc_id}/publish",
        headers={**headers, "Idempotency-Key": f"publish-{slot}"},
    )
    assert published.status_code == 200, published.text


def _review_relation(client, headers, doc_id):
    detail = client.get(f"/api/v1/gov/policies/{doc_id}", headers=headers).json()["data"]
    relation = detail["relations"][0]
    resp = client.post(
        f"/api/v1/gov/policies/relations/{relation['relation_id']}/review",
        json={"action": "approve", "note": "关系依据已核对"},
        headers={**headers, "Idempotency-Key": f"relation-{relation['relation_id']}"},
    )
    assert resp.status_code == 200, resp.text
    return relation


@pytest.fixture()
def indexed_gov(client, gov_ready, kb_headers):
    for index, label in enumerate((OLD_LABEL, NEW_LABEL)):
        resp = client.post(
            f"/api/v1/gov/policies/{DOC_ID}/versions/{label}/embeddings",
            headers={**kb_headers, "Idempotency-Key": f"embed-{index}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["embedded"] > 0
    return gov_ready


@pytest.fixture()
def gov_ready(client, seeded, kb_headers):
    first = _import(client, kb_headers, OLD_SAMPLE, "gov-old-1")
    second = _import(client, kb_headers, NEW_SAMPLE, "gov-new-1")
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    _review_relation(client, kb_headers, DOC_ID)
    _approve_and_publish(client, kb_headers, OLD_SAMPLE)
    _approve_and_publish(client, kb_headers, NEW_SAMPLE)
    return seeded


class TestGovDomainSkeleton:
    def test_should_load_gov_domain_with_compose_only(self):
        plugin = DomainRegistry.load_default().get("gov_office")
        assert plugin is not None
        assert plugin.status == "draft"
        assert plugin.hooks.CAPABILITIES == frozenset({"compose"})
        assert plugin.dimensions[0]["code"] == "G1"

    def test_should_seed_gov_domain_as_draft(self, seeded):
        row = seeded.get(DomInstance, "gov_office")
        assert row is not None
        assert row.status == "draft"
        assert row.active_revision_id is None


class TestPolicyImport:
    def test_should_import_and_repeat_same_idempotency_key(self, client, seeded, kb_headers):
        first = _import(client, kb_headers, NEW_SAMPLE, "gov-idem-1")
        second = _import(client, kb_headers, NEW_SAMPLE, "gov-idem-1")
        assert first.status_code == 201
        assert first.json()["data"]["duplicated"] is False
        assert second.status_code == 201
        assert second.json()["data"]["duplicated"] is True
        assert second.json()["data"]["version_id"] == first.json()["data"]["version_id"]

    def test_should_repeat_same_version_across_keys_without_new_row(
        self, client, seeded, kb_headers
    ):
        first = _import(client, kb_headers, NEW_SAMPLE, "gov-idem-a")
        second = _import(client, kb_headers, NEW_SAMPLE, "gov-idem-b")
        assert first.status_code == 201 and second.status_code == 201
        assert second.json()["data"]["duplicated"] is True
        body = client.get(
            f"/api/v1/gov/policies/{NEW_SAMPLE['document']['doc_id']}",
            headers=kb_headers,
        ).json()["data"]
        assert len(body["versions"]) == 1

    def test_should_reject_different_payload_for_same_idempotency_key(
        self, client, seeded, kb_headers
    ):
        assert _import(client, kb_headers, NEW_SAMPLE, "gov-idem-conflict").status_code == 201
        modified = json.loads(json.dumps(NEW_SAMPLE))
        modified["version"]["raw_text"] += "\n人工注入的额外文本"
        modified["version"]["source_hash"] = hashlib.sha256(
            modified["version"]["raw_text"].encode("utf-8")
        ).hexdigest()
        resp = _import(client, kb_headers, modified, "gov-idem-conflict")
        assert resp.status_code == 409
        assert resp.json()["code"] == "IDEMPOTENCY_CONFLICT"

    def test_should_hide_unpublished_policy_from_normal_reader(
        self, client, seeded, kb_headers, expert_headers
    ):
        assert _import(client, kb_headers, NEW_SAMPLE, "gov-hidden-draft").status_code == 201
        expert_list = client.get("/api/v1/gov/policies", headers=expert_headers)
        reviewer_list = client.get("/api/v1/gov/policies", headers=kb_headers)
        assert expert_list.json()["data"]["total"] == 0
        assert reviewer_list.json()["data"]["total"] == 1
        assert (
            client.get(f"/api/v1/gov/policies/{DOC_ID}", headers=expert_headers).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/gov/policies/{DOC_ID}/versions/{NEW_LABEL}",
                headers=expert_headers,
            ).status_code
            == 404
        )

    def test_should_reject_source_hash_mismatch(self, client, seeded, kb_headers):
        modified = json.loads(json.dumps(NEW_SAMPLE))
        modified["version"]["source_hash"] = "0" * 64
        resp = _import(client, kb_headers, modified, "gov-hash-bad")
        assert resp.status_code == 400
        assert resp.json()["code"] == "POLICY_SOURCE_HASH_MISMATCH"

    def test_should_require_idempotency_key(self, client, seeded, kb_headers):
        resp = client.post(
            "/api/v1/gov/policies/import", json=NEW_SAMPLE, headers=kb_headers
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "IDEMPOTENCY_REQUIRED"

    def test_should_forbid_expert_import(self, client, seeded, expert_headers):
        resp = _import(client, expert_headers, NEW_SAMPLE, "gov-perm")
        assert resp.status_code == 403
        assert resp.json()["code"] == "PERM_DENIED"

    def test_should_require_authentication(self, client, seeded):
        resp = client.post(
            "/api/v1/gov/policies/import",
            json=NEW_SAMPLE,
            headers={"Idempotency-Key": "gov-anon"},
        )
        assert resp.status_code == 401


class TestPolicyReviewAndPublication:
    def test_should_not_publish_without_approved_version(self, client, seeded, kb_headers):
        assert _import(client, kb_headers, NEW_SAMPLE, "gov-unreviewed").status_code == 201
        resp = client.post(
            f"/api/v1/gov/policies/{NEW_SAMPLE['document']['doc_id']}/publish",
            headers={**kb_headers, "Idempotency-Key": "publish-unreviewed"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "POLICY_PUBLISH_NO_APPROVED"

    def test_should_not_switch_rejected_version_to_approved(
        self, client, seeded, kb_headers
    ):
        assert _import(client, kb_headers, NEW_SAMPLE, "gov-reject").status_code == 201
        doc_id = NEW_SAMPLE["document"]["doc_id"]
        path = f"/api/v1/gov/policies/{doc_id}/versions/{NEW_LABEL}/review"
        rejected = client.post(
            path,
            json={"action": "reject"},
            headers={**kb_headers, "Idempotency-Key": "reject-1"},
        )
        assert rejected.status_code == 200
        approved = client.post(
            path,
            json={"action": "approve"},
            headers={**kb_headers, "Idempotency-Key": "approve-after-reject"},
        )
        assert approved.status_code == 409
        assert approved.json()["code"] == "POLICY_REVIEW_STATE_INVALID"

    def test_should_not_apply_unpublished_versions(self, client, seeded, kb_headers):
        assert _import(client, kb_headers, NEW_SAMPLE, "gov-hidden").status_code == 201
        resp = client.post(
            "/api/v1/gov/policies/applicable",
            json={"jurisdiction": "全国", "as_of": "2026-10-27"},
            headers=kb_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["applicable"] == []

    def test_should_audit_policy_review_and_publish(self, gov_ready):
        from core.gateway.models import GovAuditLog

        actions = {
            row.action
            for row in gov_ready.query(GovAuditLog)
            .filter(GovAuditLog.action.like("gov.policy.%"))
            .all()
        }
        assert "gov.policy.version.review" in actions
        assert "gov.policy.publish" in actions


class TestPolicyApplicability:
    def test_should_select_old_version_for_historical_date(
        self, client, gov_ready, expert_headers
    ):
        resp = client.post(
            "/api/v1/gov/policies/applicable",
            json={"jurisdiction": "全国", "as_of": "2018-12-31"},
            headers=expert_headers,
        )
        data = resp.json()["data"]
        assert resp.status_code == 200
        assert [item["version"]["version_id"] for item in data["applicable"]] == [
            f"{DOC_ID}:{OLD_LABEL}"
        ]

    def test_should_select_new_version_after_effective_date(
        self, client, gov_ready, expert_headers
    ):
        resp = client.post(
            "/api/v1/gov/policies/applicable",
            json={"jurisdiction": "全国", "as_of": "2020-01-01"},
            headers=expert_headers,
        )
        data = resp.json()["data"]
        assert [item["state"] for item in data["applicable"]] == ["effective"]
        assert data["applicable"][0]["version"]["version_id"] == f"{DOC_ID}:{NEW_LABEL}"
        excluded_states = {item["state"] for item in data["excluded"]}
        assert "expired" in excluded_states

    def test_should_show_new_version_not_yet_effective_on_publication_date(
        self, client, gov_ready, expert_headers
    ):
        resp = client.post(
            "/api/v1/gov/policies/applicable",
            json={"jurisdiction": "全国", "as_of": "2019-04-20"},
            headers=expert_headers,
        )
        data = resp.json()["data"]
        assert data["applicable"][0]["version"]["version_id"] == f"{DOC_ID}:{OLD_LABEL}"
        new_item = next(
            item
            for item in data["excluded"]
            if item["version"]["version_id"] == f"{DOC_ID}:{NEW_LABEL}"
        )
        assert new_item["state"] == "not_yet_effective"

    def test_should_warn_when_no_version_applies(self, client, gov_ready, expert_headers):
        resp = client.post(
            "/api/v1/gov/policies/applicable",
            json={"jurisdiction": "全国", "as_of": "2007-12-31"},
            headers=expert_headers,
        )
        data = resp.json()["data"]
        assert data["applicable"] == []
        assert data["manual_check_required"] is True
        assert data["warnings"]

    def test_should_exclude_subject_outside_approved_applicability(
        self, client, gov_ready, expert_headers
    ):
        meta = dict(gov_ready.get(GovPolicyDocument, DOC_ID).meta or {})
        meta["applicability"] = {"subjects": ["行政机关"]}
        gov_ready.get(GovPolicyDocument, DOC_ID).meta = meta
        gov_ready.commit()

        resp = client.post(
            "/api/v1/gov/policies/applicable",
            json={
                "jurisdiction": "全国",
                "as_of": "2020-01-01",
                "subject": "企业法人",
            },
            headers=expert_headers,
        )
        data = resp.json()["data"]
        assert data["applicable"] == []
        states = {item["state"] for item in data["excluded"]}
        assert "subject_not_applicable" in states

    def test_should_include_approved_version_relation(
        self, client, gov_ready, expert_headers
    ):
        resp = client.post(
            "/api/v1/gov/policies/applicable",
            json={"jurisdiction": "全国", "as_of": "2020-01-01"},
            headers=expert_headers,
        )
        relations = resp.json()["data"]["relations"]
        assert len(relations) == 1
        assert relations[0]["relation_type"] == "replaced_by"
        assert relations[0]["review_status"] == "approved"


class TestClauseCorrectionAndRelationReview:
    def test_should_create_and_update_draft_clause_with_revision(
        self, client, seeded, kb_headers
    ):
        assert _import(client, kb_headers, NEW_SAMPLE, "gov-edit-import").status_code == 201
        path = (
            f"/api/v1/gov/policies/{DOC_ID}/versions/{NEW_LABEL}"
            "/clauses/新增条款"
        )
        added = client.put(
            path,
            json={
                "content": "新增条款正文（工程测试）",
                "path": "第一章 总则",
                "expected_revision": 0,
            },
            headers={**kb_headers, "Idempotency-Key": "clause-add-1"},
        )
        assert added.status_code == 200, added.text
        assert added.json()["data"]["revision"] == 1
        assert added.json()["data"]["clause"]["seq"] == len(NEW_SAMPLE["clauses"]) + 1

        changed = client.put(
            path,
            json={
                "content": "更新后的条款正文（工程测试）",
                "path": "第一章 总则",
                "expected_revision": 1,
            },
            headers={**kb_headers, "Idempotency-Key": "clause-update-2"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["data"]["revision"] == 2
        assert "更新后" in changed.json()["data"]["clause"]["content"]

    def test_should_treat_identical_clause_update_as_idempotent(
        self, client, seeded, kb_headers
    ):
        assert _import(client, kb_headers, NEW_SAMPLE, "gov-edit-idem").status_code == 201
        path = (
            f"/api/v1/gov/policies/{DOC_ID}/versions/{NEW_LABEL}"
            "/clauses/第二条"
        )
        body = {
            "content": "第二条 工程幂等测试正文",
            "path": "第一章 总则",
            "expected_revision": 0,
        }
        first = client.put(
            path,
            json=body,
            headers={**kb_headers, "Idempotency-Key": "same-clause-1"},
        )
        second = client.put(
            path,
            json=body,
            headers={**kb_headers, "Idempotency-Key": "same-clause-2"},
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["data"]["duplicated"] is True
        assert second.json()["data"]["revision"] == 1

    def test_should_reject_stale_clause_revision(self, client, seeded, kb_headers):
        assert _import(client, kb_headers, NEW_SAMPLE, "gov-edit-stale").status_code == 201
        path = (
            f"/api/v1/gov/policies/{DOC_ID}/versions/{NEW_LABEL}"
            "/clauses/第二条"
        )
        first = client.put(
            path,
            json={"content": "第一次修改", "expected_revision": 0},
            headers={**kb_headers, "Idempotency-Key": "stale-1"},
        )
        assert first.status_code == 200
        stale = client.put(
            path,
            json={"content": "第二次修改", "expected_revision": 0},
            headers={**kb_headers, "Idempotency-Key": "stale-2"},
        )
        assert stale.status_code == 412
        assert stale.json()["code"] == "PRECONDITION_FAILED"

    def test_should_block_clause_update_after_review(
        self, client, gov_ready, kb_headers
    ):
        path = (
            f"/api/v1/gov/policies/{DOC_ID}/versions/{NEW_LABEL}"
            "/clauses/第二条"
        )
        resp = client.put(
            path,
            json={"content": "审核后不可修改", "expected_revision": 0},
            headers={**kb_headers, "Idempotency-Key": "approved-edit"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "POLICY_VERSION_IMMUTABLE"

    def test_should_reject_relation_to_missing_target(
        self, client, seeded, kb_headers
    ):
        assert _import(client, kb_headers, OLD_SAMPLE, "gov-relation-orphan").status_code == 201
        detail = client.get(f"/api/v1/gov/policies/{DOC_ID}", headers=kb_headers).json()["data"]
        relation_id = detail["relations"][0]["relation_id"]
        resp = client.post(
            f"/api/v1/gov/policies/relations/{relation_id}/review",
            json={"action": "approve"},
            headers={**kb_headers, "Idempotency-Key": "orphan-relation"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "POLICY_RELATION_TARGET_MISSING"


class TestPolicySearchAndCompare:
    def test_should_search_effective_clause_only(self, client, gov_ready, expert_headers):
        resp = client.post(
            "/api/v1/gov/policies/search",
            json={
                "query": "政府信息 主动公开",
                "jurisdiction": "全国",
                "as_of": "2020-01-01",
                "top_k": 5,
            },
            headers=expert_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        items = data["items"]
        assert items
        assert data["retrieval"]["mode"] == "lexical"
        assert all(item["version"]["version_id"] == f"{DOC_ID}:{NEW_LABEL}" for item in items)
        assert all(item["clause"]["citation_id"] for item in items)
        assert items[0]["clause"]["content"]

    def test_should_search_in_hybrid_mode_after_embeddings_built(
        self, client, indexed_gov, expert_headers
    ):
        resp = client.post(
            "/api/v1/gov/policies/search",
            json={
                "query": "政府信息 主动公开",
                "jurisdiction": "全国",
                "as_of": "2020-01-01",
                "top_k": 5,
            },
            headers=expert_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["retrieval"]["mode"] == "hybrid"
        assert data["retrieval"]["embedding_model"]
        assert data["items"]
        assert all(item["vector_score"] >= 0 for item in data["items"])

    def test_should_reject_embeddings_for_unreviewed_version(
        self, client, seeded, kb_headers
    ):
        assert _import(client, kb_headers, NEW_SAMPLE, "gov-embed-draft").status_code == 201
        resp = client.post(
            f"/api/v1/gov/policies/{DOC_ID}/versions/{NEW_LABEL}/embeddings",
            headers={**kb_headers, "Idempotency-Key": "embed-draft"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "POLICY_REVIEW_STATE_INVALID"

    def test_should_search_expired_version_for_historical_date(
        self, client, gov_ready, expert_headers
    ):
        resp = client.post(
            "/api/v1/gov/policies/search",
            json={
                "query": "行政机关 政府信息 公开",
                "jurisdiction": "全国",
                "as_of": "2018-12-31",
                "top_k": 5,
            },
            headers=expert_headers,
        )
        items = resp.json()["data"]["items"]
        assert items
        assert all(item["version"]["version_id"] == f"{DOC_ID}:{OLD_LABEL}" for item in items)

    def test_should_compare_changed_removed_and_added_clauses(
        self, client, gov_ready, expert_headers
    ):
        resp = client.get(
            f"/api/v1/gov/policies/{OLD_SAMPLE['document']['doc_id']}"
            f"/versions/{OLD_LABEL}/compare",
            params={"other": NEW_LABEL},
            headers=expert_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["summary"]["added"] > 0
        changed_numbers = {item["clause_no"] for item in data["changed"]}
        assert "第二条" in changed_numbers

    def test_should_return_404_for_unknown_policy(self, client, gov_ready, expert_headers):
        resp = client.get("/api/v1/gov/policies/GOV-UNKNOWN", headers=expert_headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == "POLICY_NOT_FOUND"

    def test_should_list_policies_with_pagination(self, client, gov_ready, expert_headers):
        resp = client.get(
            "/api/v1/gov/policies?page=1&page_size=1", headers=expert_headers
        )
        data = resp.json()["data"]
        assert resp.status_code == 200
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["page_size"] == 1


class TestPolicySupportCheck:
    def _citation(self, client, headers):
        search = client.post(
            "/api/v1/gov/policies/search",
            json={
                "query": "政府信息 主动公开",
                "jurisdiction": "全国",
                "as_of": "2020-01-01",
                "top_k": 1,
            },
            headers=headers,
        ).json()["data"]["items"][0]["clause"]
        return search["citation_id"], search["content"]

    def test_should_accept_exact_clause_quote(self, client, gov_ready, expert_headers):
        citation_id, content = self._citation(client, expert_headers)
        resp = client.post(
            "/api/v1/gov/policies/support-check",
            json={
                "claim": f"《政府信息公开条例》规定：{content}",
                "citation_ids": [citation_id],
            },
            headers=expert_headers,
        )
        data = resp.json()["data"]
        assert resp.status_code == 200
        assert data["support_status"] == "direct"
        assert data["support_method"] == "exact_clause_quote"
        assert data["manual_review_required"] is False

    def test_should_flag_partial_support(self, client, gov_ready, expert_headers):
        citation_id, content = self._citation(client, expert_headers)
        altered = content.replace("应当", "可以", 1)
        resp = client.post(
            "/api/v1/gov/policies/support-check",
            json={
                "claim": f"《政府信息公开条例》规定：{altered}",
                "citation_ids": [citation_id],
            },
            headers=expert_headers,
        )
        data = resp.json()["data"]
        assert resp.status_code == 200
        assert data["support_status"] in {"partial", "insufficient"}
        assert data["manual_review_required"] is True

    def test_should_reject_unknown_or_unpublished_citation(
        self, client, gov_ready, expert_headers
    ):
        resp = client.post(
            "/api/v1/gov/policies/support-check",
            json={"claim": "任意结论", "citation_ids": ["CL-unknown@v1"]},
            headers=expert_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "POLICY_CLAUSE_NOT_FOUND"

    def test_should_not_let_semantic_model_upgrade_word_level_support(
        self, client, gov_ready, expert_headers, monkeypatch
    ):
        citation_id, content = self._citation(client, expert_headers)
        altered = content.replace("应当", "可以", 1)

        class FakeGateway:
            async def chat(self, *args, **kwargs):
                return SimpleNamespace(
                    content='{"status":"supported","rationale":"语义相近"}',
                    model="fake-semantic",
                    degrade_level="L0",
                )

        monkeypatch.setattr(
            gov_services, "get_model_gateway", lambda: FakeGateway()
        )
        resp = client.post(
            "/api/v1/gov/policies/support-check",
            json={
                "claim": f"《政府信息公开条例》规定：{altered}",
                "citation_ids": [citation_id],
                "use_semantic": True,
            },
            headers=expert_headers,
        )
        data = resp.json()["data"]
        assert resp.status_code == 200
        assert data["support_status"] != "direct"
        assert data["semantic_review"]["status"] == "supported"
        assert data["manual_review_required"] is True


class TestPolicyInterpretation:
    def test_should_clarify_when_jurisdiction_or_date_missing(
        self, client, gov_ready, expert_headers
    ):
        resp = client.post(
            "/api/v1/gov/policies/interpret",
            json={"question": "哪些政府信息应当主动公开？"},
            headers=expert_headers,
        )
        data = resp.json()["data"]
        assert resp.status_code == 200
        assert data["outcome"] == "clarify"
        assert {item["field"] for item in data["clarifications"]} == {
            "政策地区",
            "事项时间",
        }
        assert data["claims"] == []

    def test_should_answer_with_direct_clause_claims_and_citations(
        self, client, gov_ready, expert_headers
    ):
        resp = client.post(
            "/api/v1/gov/policies/interpret",
            json={
                "question": "哪些政府信息应当主动公开？",
                "jurisdiction": "全国",
                "as_of": "2020-01-01",
            },
            headers=expert_headers,
        )
        data = resp.json()["data"]
        assert resp.status_code == 200
        assert data["outcome"] == "answer"
        assert data["clarifications"] == []
        assert data["claims"]
        assert all(claim["support_status"] == "direct" for claim in data["claims"])
        assert all(claim["support_method"] == "exact_clause_quote" for claim in data["claims"])
        assert all(claim["citation_ids"] for claim in data["claims"])
        citation_ids = {item["citation_id"] for item in data["citations"]}
        assert all(
            set(claim["citation_ids"]) <= citation_ids for claim in data["claims"]
        )
        assert data["unverified_claim_ids"] == []
        assert data["manual_review_required"] is True

    def test_should_return_insufficient_when_no_effective_clause(
        self, client, gov_ready, expert_headers
    ):
        resp = client.post(
            "/api/v1/gov/policies/interpret",
            json={
                "question": "火星基地建设工程验收需要哪些材料？",
                "jurisdiction": "全国",
                "as_of": "2020-01-01",
            },
            headers=expert_headers,
        )
        data = resp.json()["data"]
        assert resp.status_code == 200
        assert data["outcome"] == "insufficient"
        assert data["claims"] == []
        assert data["warnings"]
