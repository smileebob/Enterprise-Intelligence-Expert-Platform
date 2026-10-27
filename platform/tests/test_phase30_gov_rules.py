"""Phase 30 · 公文硬规则样例完整性与模板同步。"""

from __future__ import annotations

from pathlib import Path

from app.gov_office.documents import load_rules

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_RULES = ROOT / "政务智能体" / "templates" / "gov_document_rules.yaml"
DOMAIN_RULES = (
    Path(__file__).resolve().parents[1]
    / "domains"
    / "gov_office"
    / "templates"
    / "gov_document_rules.yaml"
)
IMPLEMENTED_CODES = {
    "BODY_TOO_SHORT",
    "UNSOURCED_NUMBER",
    "REPORT_CONTAINS_REQUEST",
    "REQUIRED_FIELD_MISSING",
    "MINUTES_WITHOUT_RECORD",
    "POLICY_CITATION_INVALID",
}


class TestGovernmentDocumentRules:
    def test_should_provide_positive_and_negative_example_for_every_doc_rule(self):
        rules = load_rules()
        examples = rules["rule_examples"]
        for doc_type, template in rules["document_types"].items():
            hard_rules = template["hard_rules"]
            typed_examples = examples[doc_type]
            assert len(typed_examples) == len(hard_rules), doc_type
            assert [item["rule"] for item in typed_examples] == hard_rules
            for item in typed_examples:
                assert item["valid"]
                assert item["invalid"]
                assert item["mode"] in {"deterministic", "human_review"}
                if item["mode"] == "deterministic":
                    assert item["expected_code"] in IMPLEMENTED_CODES

    def test_should_provide_examples_for_every_global_check(self):
        rules = load_rules()
        examples = rules["rule_examples"]["global"]
        assert len(examples) == len(rules["global_checks"])
        for name, rules_for_check in rules["global_checks"].items():
            assert rules_for_check
            assert any(name.upper() in item["rule_id"] for item in examples)

    def test_should_keep_domain_rules_in_sync_with_canonical_document(self):
        assert CANONICAL_RULES.read_text(encoding="utf-8") == DOMAIN_RULES.read_text(
            encoding="utf-8"
        )
