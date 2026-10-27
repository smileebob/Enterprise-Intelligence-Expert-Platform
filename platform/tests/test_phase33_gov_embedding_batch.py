"""Phase 33 · 嵌入分批：避免供应商单请求批量上限导致回退。"""

from __future__ import annotations

import json
from pathlib import Path

import app.gov_office.services as gov_services

ROOT = Path(__file__).resolve().parents[2]
NEW_SAMPLE = json.loads(
    (ROOT / "政务智能体" / "samples" / "public" / "GOV-REG-711-2019.json").read_text(
        encoding="utf-8"
    )
)


class FakeEmbeddingGateway:
    embed_model_name = "fake-embed-v1"

    def __init__(self) -> None:
        self.batches: list[int] = []

    async def embed(self, texts: list[str], **_: object) -> list[list[float]]:
        self.batches.append(len(texts))
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def test_should_batch_embeddings_under_provider_limit(
    client, gov_ready, kb_headers, monkeypatch
):
    fake = FakeEmbeddingGateway()
    monkeypatch.setattr(gov_services, "get_model_gateway", lambda: fake)
    doc_id = NEW_SAMPLE["document"]["doc_id"]
    label = NEW_SAMPLE["version"]["version_label"]
    resp = client.post(
        f"/api/v1/gov/policies/{doc_id}/versions/{label}/embeddings",
        headers={**kb_headers, "Idempotency-Key": "embed-batch-1"},
    )
    assert resp.status_code == 200, resp.text
    embedded = resp.json()["data"]["embedded"]
    assert embedded == len(NEW_SAMPLE["clauses"])
    assert fake.batches
    assert max(fake.batches) <= gov_services.EMBED_BATCH_SIZE
    assert sum(fake.batches) == embedded
