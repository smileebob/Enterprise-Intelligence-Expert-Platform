"""Phase 15 · 02 领域配置 revision/CAS/v2（R01–R04 的 SQLite 等价验证）。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.models  # noqa: F401 - 注册全部 ORM 模型
from app.db import Base
from core.abstraction.dtos import Actor
from core.abstraction.enums import Roles
from core.admin.models import DomInstance, DomRevision
from core.admin.service import AdminService, PreconditionFailed
from core.runtime import DbDomainRegistry
from tests.conftest import seed_users_and_permissions


def _admin() -> Actor:
    return Actor(
        user_id="u-admin",
        username="admin",
        role_id=Roles.PLATFORM_ADMIN,
        permission_max=2,
        domain_scope=["chemical", "investment"],
    )


def _chemical_config(name: str = "") -> dict:
    config = yaml.safe_load(Path("domains/chemical/domain.yaml").read_text(encoding="utf-8"))
    if name:
        config["name"] = name
    return config


def _v2_write_headers(headers: dict[str, str], *, rev: int | None, idem: str | None) -> dict[str, str]:
    merged = dict(headers)
    if rev is not None:
        merged["If-Match"] = f'"{rev}"'
    if idem is not None:
        merged["Idempotency-Key"] = idem
    return merged


@pytest.fixture()
def file_db(tmp_path):
    """共享 SQLite 文件库：模拟两个独立 worker 各自持有 Session。"""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'revisions.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    bootstrap = factory()
    seed_users_and_permissions(bootstrap)
    bootstrap.close()
    yield factory
    engine.dispose()


class TestV2Contract:
    def test_should_require_if_match_and_idempotency_key(self, client, seeded, admin_headers):
        resp = client.put(
            "/api/v2/domains/chemical/draft",
            json={"config": _chemical_config()},
            headers=admin_headers,
        )
        assert resp.status_code == 428
        assert resp.json()["code"] == "PRECONDITION_REQUIRED"

        resp = client.put(
            "/api/v2/domains/chemical/draft",
            json={"config": _chemical_config()},
            headers=_v2_write_headers(admin_headers, rev=1, idem=None),
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "IDEMPOTENCY_REQUIRED"

    def test_should_expose_etag_and_revision_history(self, client, seeded, admin_headers):
        resp = client.get("/api/v2/domains/chemical", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert resp.headers["ETag"] == f'"{body["config_rev"]}"'
        revisions = client.get("/api/v2/domains/chemical/revisions", headers=admin_headers)
        assert revisions.status_code == 200
        assert revisions.json()["data"]

    def test_should_reject_stale_if_match(self, client, seeded, admin_headers):
        current = client.get("/api/v2/domains/chemical", headers=admin_headers).json()["data"]["config_rev"]
        first = client.put(
            "/api/v2/domains/chemical/draft",
            json={"config": _chemical_config("化学领域草稿A")},
            headers=_v2_write_headers(admin_headers, rev=current, idem="idem-stale-a"),
        )
        assert first.status_code == 200, first.text
        stale = client.put(
            "/api/v2/domains/chemical/draft",
            json={"config": _chemical_config("化学领域草稿B")},
            headers=_v2_write_headers(admin_headers, rev=current, idem="idem-stale-b"),
        )
        assert stale.status_code == 412
        assert stale.json()["code"] == "PRECONDITION_FAILED"

    def test_should_replay_same_request_and_reject_changed_payload(self, client, seeded, admin_headers):
        current = client.get("/api/v2/domains/chemical", headers=admin_headers).json()["data"]["config_rev"]
        payload = {"config": _chemical_config("化学领域幂等草稿")}
        headers = _v2_write_headers(admin_headers, rev=current, idem="idem-replay-1")

        first = client.put("/api/v2/domains/chemical/draft", json=payload, headers=headers)
        assert first.status_code == 200, first.text
        second = client.put("/api/v2/domains/chemical/draft", json=payload, headers=headers)
        assert second.status_code == 200
        assert second.json()["data"] == first.json()["data"]

        changed = {"config": _chemical_config("化学领域幂等草稿-改")}
        conflict = client.put(
            "/api/v2/domains/chemical/draft",
            json=changed,
            headers=_v2_write_headers(admin_headers, rev=current, idem="idem-replay-1"),
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

    def test_should_keep_active_config_until_draft_published(self, client, seeded, db_session, admin_headers):
        active_before = client.get("/api/v2/domains/chemical", headers=admin_headers).json()["data"]
        rev = active_before["config_rev"]
        draft = client.put(
            "/api/v2/domains/chemical/draft",
            json={"config": _chemical_config("化学领域新草稿")},
            headers=_v2_write_headers(admin_headers, rev=rev, idem="idem-draft-online"),
        )
        assert draft.status_code == 200, draft.text
        after_draft = client.get("/api/v2/domains/chemical", headers=admin_headers).json()["data"]
        assert after_draft["status"] == "online"
        assert after_draft["name"] == active_before["name"]
        assert after_draft["active_revision_id"] == active_before["active_revision_id"]
        assert after_draft["draft_revision_id"] == draft.json()["data"]["draft_revision_id"]
        assert after_draft["config_rev"] == rev + 1

        publish = client.post(
            "/api/v2/domains/chemical/publish",
            json={"revision_id": draft.json()["data"]["draft_revision_id"]},
            headers=_v2_write_headers(admin_headers, rev=rev + 1, idem="idem-publish-online"),
        )
        assert publish.status_code == 200, publish.text
        registry = DbDomainRegistry(db_session)
        assert registry.require("chemical").name == "化学领域新草稿"


class TestCasAndRollback:
    def test_should_reject_second_writer_with_same_expected_revision(self, file_db):
        session_a = file_db()
        session_b = file_db()
        try:
            rev = session_a.get(DomInstance, "chemical").config_rev
            config = _chemical_config("并发写入A")
            AdminService(session_a).submit_domain(
                config, _admin(), expected_rev=rev, idem_key="idem-worker-a"
            )
            with pytest.raises(PreconditionFailed):
                AdminService(session_b).submit_domain(
                    _chemical_config("并发写入B"),
                    _admin(),
                    expected_rev=rev,
                    idem_key="idem-worker-b",
                )
            session_b.rollback()
            detail = AdminService(session_b).get_domain("chemical")
            assert detail["config_rev"] == rev + 1
            assert detail["draft_revision_id"]
        finally:
            session_a.close()
            session_b.close()

    def test_should_rollback_to_larger_revision_matching_history(self, file_db):
        session = file_db()
        try:
            service = AdminService(session)
            original = service.get_domain("chemical")
            seed_revision = original["active_revision_id"]
            seed_hash = session.get(DomRevision, seed_revision).content_hash
            seed_rev = original["config_rev"]

            service.submit_domain(
                _chemical_config("化学领域第二版"),
                _admin(),
                expected_rev=seed_rev,
                idem_key="idem-rollback-draft",
            )
            draft = service.get_domain("chemical")["draft_revision_id"]
            service.publish_domain(
                "chemical", _admin(), expected_rev=seed_rev + 1, revision_id=draft, idem_key="idem-rollback-pub"
            )
            published = service.get_domain("chemical")
            assert published["config_rev"] == seed_rev + 2

            result = service.rollback_domain(
                "chemical",
                _admin(),
                revision_id=seed_revision,
                expected_rev=seed_rev + 2,
                idem_key="idem-rollback-back",
            )
            assert result["config_rev"] == seed_rev + 3
            assert result["restored_from"] == seed_revision
            active = session.get(DomRevision, result["active_revision_id"])
            assert active.content_hash == seed_hash
            assert active.config["name"] == _chemical_config()["name"]
            assert service.get_domain("chemical")["config_rev"] > seed_rev + 2
        finally:
            session.close()

    def test_should_reject_publish_when_domain_already_online(self, client, seeded, admin_headers):
        rev = client.get("/api/v2/domains/chemical", headers=admin_headers).json()["data"]["config_rev"]
        resp = client.post(
            "/api/v2/domains/chemical/publish",
            json={},
            headers=_v2_write_headers(admin_headers, rev=rev, idem="idem-publish-online-conflict"),
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "STATE_INVALID"


class TestRuntimeResolution:
    def test_should_resolve_latest_revision_across_sessions(self, file_db):
        writer = file_db()
        reader = file_db()
        try:
            writer_rev = writer.get(DomInstance, "chemical").config_rev
            service = AdminService(writer)
            service.submit_domain(
                _chemical_config("跨进程可见的新名称"),
                _admin(),
                expected_rev=writer_rev,
                idem_key="idem-cross-draft",
            )
            draft = service.get_domain("chemical")["draft_revision_id"]
            service.publish_domain(
                "chemical",
                _admin(),
                expected_rev=writer_rev + 1,
                revision_id=draft,
                idem_key="idem-cross-publish",
            )
            registry = DbDomainRegistry(reader)
            assert registry.require("chemical").name == "跨进程可见的新名称"
        finally:
            writer.close()
            reader.close()

    def test_should_not_overwrite_admin_changes_when_seed_reruns(self, file_db):
        session = file_db()
        try:
            service = AdminService(session)
            rev = service.get_domain("chemical")["config_rev"]
            service.offline_domain("chemical", _admin(), expected_rev=rev, idem_key="idem-seed-offline")
            assert service.get_domain("chemical")["status"] == "offline"
            seed_users_and_permissions(session)  # 重复 seed 不得把状态恢复为 online
            assert service.get_domain("chemical")["status"] == "offline"
            assert session.execute(select(func.count()).select_from(DomRevision)).scalar() >= 2
        finally:
            session.close()
