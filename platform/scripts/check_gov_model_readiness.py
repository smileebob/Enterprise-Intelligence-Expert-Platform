"""G-23 模型凭证就绪检查（不伪造模型结果）。

默认只做静态检查，不调用外部服务。加 --probe 才会发一条最小对话和一条
嵌入请求，用于确认凭证、端点、模型名和向量维度；会产生少量费用。

用法（platform 目录）：
    ./.venv/Scripts/python.exe scripts/check_gov_model_readiness.py
    ./.venv/Scripts/python.exe scripts/check_gov_model_readiness.py --probe --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

PLATFORM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM_ROOT))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402

PLACEHOLDERS = {
    "",
    "change-me",
    "your-key",
    "your_api_key",
    "sk-xxx",
    "sk-your-key",
    "replace-me",
}


@dataclass
class Readiness:
    ready: bool
    probe_ready: bool
    demo_mode: str
    base_url: str
    chat_model: str
    embed_model: str
    embed_dim: int
    key_masked: str
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    probe: dict = field(default_factory=dict)


def mask_secret(value: str) -> str:
    if not value:
        return "(未设置)"
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def looks_like_access_key(value: str) -> bool:
    return value.startswith("LTAI") or (
        len(value) == 30 and value.isalnum() and not value.startswith("sk-")
    )


def endpoint_ok(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"}:
        return False
    return True


def static_check() -> Readiness:
    settings = get_settings()
    key = os.getenv("DASHSCOPE_API_KEY", "").strip() or settings.LLM_API_KEY.strip()
    issues: list[str] = []
    warnings: list[str] = []

    if settings.DEMO_MODE != "live":
        warnings.append("DEMO_MODE 不是 live；当前不会走真实模型通道")
    if not key or key.lower() in PLACEHOLDERS:
        issues.append("缺少模型 API Key：请设置 DASHSCOPE_API_KEY 或 LLM_API_KEY")
    elif looks_like_access_key(key):
        issues.append(
            "当前值像阿里云 AccessKey ID/Secret，不是百炼模型 API Key；"
            "请在百炼控制台创建 API Key"
        )
    if not endpoint_ok(settings.LLM_BASE_URL):
        issues.append("LLM_BASE_URL 必须是 https，或本机 http://localhost")
    if not settings.LLM_MODEL.strip():
        issues.append("缺少 LLM_MODEL")
    if not settings.EMBED_MODEL.strip():
        issues.append("缺少 EMBED_MODEL")
    if settings.EMBED_DIM <= 0:
        issues.append("EMBED_DIM 必须为正整数")
    if os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET") or os.getenv("ALIYUN_ACCESS_KEY_SECRET"):
        warnings.append(
            "检测到 AccessKey Secret 环境变量；模型推理不使用 AK/SK，请改用百炼 API Key"
        )

    return Readiness(
        ready=not issues,
        probe_ready=False,
        demo_mode=settings.DEMO_MODE,
        base_url=settings.LLM_BASE_URL,
        chat_model=settings.LLM_MODEL,
        embed_model=settings.EMBED_MODEL,
        embed_dim=settings.EMBED_DIM,
        key_masked=mask_secret(key),
        issues=issues,
        warnings=warnings,
    )


async def probe(settings) -> dict:
    key = os.getenv("DASHSCOPE_API_KEY", "").strip() or settings.LLM_API_KEY.strip()
    base = settings.LLM_BASE_URL.rstrip("/")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    result: dict = {}
    timeout = httpx.Timeout(connect=10.0, read=settings.LLM_TIMEOUT_S, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        started = __import__("time").perf_counter()
        try:
            resp = await client.post(
                f"{base}/chat/completions",
                headers=headers,
                json={
                    "model": settings.LLM_MODEL,
                    "messages": [{"role": "user", "content": "只回复 OK"}],
                    "temperature": 0,
                    "max_tokens": 8,
                },
            )
            latency = round((__import__("time").perf_counter() - started) * 1000, 2)
            body = resp.json()
            content = ""
            if resp.status_code == 200:
                choices = body.get("choices") or []
                if choices:
                    content = (choices[0].get("message") or {}).get("content", "")
            result["chat"] = {
                "status": resp.status_code,
                "latency_ms": latency,
                "reply": content[:40],
                "error": "" if resp.status_code == 200 else str(body)[:200],
            }
        except Exception as exc:
            result["chat"] = {"status": 0, "latency_ms": 0, "reply": "", "error": str(exc)[:200]}

        started = __import__("time").perf_counter()
        try:
            resp = await client.post(
                f"{base}/embeddings",
                headers=headers,
                json={"model": settings.EMBED_MODEL, "input": ["测试"]},
            )
            latency = round((__import__("time").perf_counter() - started) * 1000, 2)
            body = resp.json()
            vector_len = 0
            if resp.status_code == 200:
                data = body.get("data") or []
                if data:
                    vector_len = len(data[0].get("embedding") or [])
            result["embedding"] = {
                "status": resp.status_code,
                "latency_ms": latency,
                "vector_len": vector_len,
                "expected_dim": settings.EMBED_DIM,
                "dim_ok": vector_len == settings.EMBED_DIM,
                "error": "" if resp.status_code == 200 else str(body)[:200],
            }
        except Exception as exc:
            result["embedding"] = {
                "status": 0,
                "latency_ms": 0,
                "vector_len": 0,
                "expected_dim": settings.EMBED_DIM,
                "dim_ok": False,
                "error": str(exc)[:200],
            }
    return result


def print_text(report: Readiness) -> None:
    print("模型凭证就绪检查")
    print(f"- 模式：{report.demo_mode}")
    print(f"- 端点：{report.base_url}")
    print(f"- 对话模型：{report.chat_model}")
    print(f"- 嵌入模型：{report.embed_model}（期望 {report.embed_dim} 维）")
    print(f"- Key：{report.key_masked}")
    if report.issues:
        print("\n阻塞项：")
        for item in report.issues:
            print(f"- {item}")
    if report.warnings:
        print("\n提示：")
        for item in report.warnings:
            print(f"- {item}")
    if report.probe:
        print("\n探测结果：")
        for name, data in report.probe.items():
            print(f"- {name}: {json.dumps(data, ensure_ascii=False)}")
    print("\n结论：", "READY" if report.ready else "NOT READY")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="发最小请求验证凭证和模型")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    report = static_check()
    if args.probe and report.ready:
        settings = get_settings()
        report.probe = asyncio.run(probe(settings))
        report.probe_ready = bool(
            report.probe.get("chat", {}).get("status") == 200
            and report.probe.get("embedding", {}).get("dim_ok")
        )

    if args.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print_text(report)

    if not report.ready:
        return 2
    if args.probe and not report.probe_ready:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
