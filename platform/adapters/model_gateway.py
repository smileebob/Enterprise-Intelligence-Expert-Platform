"""模型网关适配器（技术方案 02 §2.3 / 06 §6.5）。

- OpenAiCompatGateway：任意 OpenAI 兼容端点（百炼 qwen / Ollama / vLLM）；超时+tenacity 重试+pybreaker 熔断+token 计量；
- OfflineModelGateway：确定性本地兜底（SHA256 哈希嵌入 + 规则通道），承接 L1/L2 与 DEMO_MODE=offline/replay；
- ResilientGateway：主通道失败自动降级（L1 简化分析 / L2 AI 暂不可用）；
- 全平台只有本模块知道具体厂商。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from pathlib import Path
from typing import Any

import httpx
import pybreaker
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from adapters.tokenize import tokenize
from core.abstraction.dtos import ChatResult
from core.abstraction.errors import LlmUnavailableError
from core.metrics import LLM_CALL_DURATION, LLM_FAILURES, LLM_TOKENS

logger = logging.getLogger(__name__)


class OfflineModelGateway:
    """离线确定性网关（零云依赖）：哈希嵌入 + 规则通道。"""

    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.embed_model_name = f"offline-hash-{dim}"

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model_alias: str = "default",
        temperature: float = 0.0,
        json_mode: bool = False,
        timeout_s: float | None = None,
    ) -> ChatResult:
        if json_mode:
            # 校准协议约定：无模型时返回空校准集（不应用任何增量）
            content = json.dumps({"calibrations": []}, ensure_ascii=False)
        else:
            content = "【简化分析】模型网关离线运行：基于规则组装检索证据生成结论。"
        return ChatResult(content=content, model="offline-rule", degrade_level="L1")

    async def embed(self, texts: list[str], *, model_alias: str = "embed") -> list[list[float]]:
        return [self._hash_embed(t) for t in texts]

    def _hash_embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = tokenize(text)
        if not tokens:
            return vec
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign * (1.0 + 0.05 * min(len(token), 10))
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    async def rerank(self, query: str, docs: list[str], *, model_alias: str = "rerank") -> list[float]:
        q = set(tokenize(query))
        scores = []
        for doc in docs:
            d = set(tokenize(doc))
            scores.append(len(q & d) / max(1, len(q)))
        return scores


class OpenAiCompatGateway:
    """OpenAI 兼容协议网关（百炼 qwen / Ollama / vLLM 均可挂载）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        embed_model: str,
        rerank_model: str = "",
        timeout_s: float = 30.0,
    ):
        self.model = model
        self.embed_model = embed_model
        self.rerank_model = rerank_model
        self.breaker = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=30)
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=httpx.Timeout(connect=5.0, read=timeout_s, write=10.0, pool=5.0),
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential_jitter(multiplier=0.5, max=4),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)),
        reraise=True,
    )
    async def _post(self, path: str, payload: dict[str, Any], timeout_s: float | None = None) -> dict[str, Any]:
        # 按请求生效超时（None 用客户端默认）；推理型模型需更长读超时，由调用方/配置决定
        kwargs: dict[str, Any] = {} if timeout_s is None else {"timeout": timeout_s}
        resp = await self.client.post(path, json=payload, **kwargs)
        if resp.status_code >= 500:
            raise httpx.RemoteProtocolError("upstream 5xx", request=resp.request)
        resp.raise_for_status()
        return resp.json()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model_alias: str = "default",
        temperature: float = 0.0,
        json_mode: bool = False,
        timeout_s: float | None = None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        try:
            data = await self.breaker.call_async(self._post, "/chat/completions", payload, timeout_s)
        except pybreaker.CircuitBreakerError as exc:
            raise LlmUnavailableError("circuit breaker open") from exc
        except httpx.HTTPError as exc:
            # 覆盖 Timeout/Connect/Status/RemoteProtocol(5xx 重试后) 等全部 httpx 异常 → 统一走降级链
            raise LlmUnavailableError(str(exc)) from exc
        LLM_CALL_DURATION.observe(time.perf_counter() - started)
        usage = data.get("usage", {})
        choice = (data.get("choices") or [{}])[0]
        model_name = str(data.get("model", self.model))
        LLM_TOKENS.labels(model=model_name, type="input").inc(int(usage.get("prompt_tokens", 0)))
        LLM_TOKENS.labels(model=model_name, type="output").inc(int(usage.get("completion_tokens", 0)))
        return ChatResult(
            content=(choice.get("message") or {}).get("content", ""),
            model=model_name,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            degrade_level="L0",
            raw=data,
        )

    async def embed(self, texts: list[str], *, model_alias: str = "embed") -> list[list[float]]:
        started = time.perf_counter()
        try:
            data = await self.breaker.call_async(self._post, "/embeddings", {"model": self.embed_model, "input": texts})
        except (pybreaker.CircuitBreakerError, httpx.HTTPError) as exc:
            raise LlmUnavailableError(str(exc)) from exc
        LLM_CALL_DURATION.observe(time.perf_counter() - started)
        return [item["embedding"] for item in sorted(data["data"], key=lambda x: x.get("index", 0))]

    async def rerank(self, query: str, docs: list[str], *, model_alias: str = "rerank") -> list[float]:
        if not self.rerank_model:
            raise LlmUnavailableError("rerank model not configured")
        started = time.perf_counter()
        try:
            data = await self.breaker.call_async(
                self._post,
                "/reranks",
                {"model": self.rerank_model, "query": query, "documents": docs},
            )
        except (pybreaker.CircuitBreakerError, httpx.HTTPError) as exc:
            raise LlmUnavailableError(str(exc)) from exc
        LLM_CALL_DURATION.observe(time.perf_counter() - started)
        results = sorted(data.get("results", []), key=lambda x: x.get("index", 0))
        return [float(r.get("relevance_score", 0.0)) for r in results]


class ReplayGateway(OfflineModelGateway):
    """DEMO_MODE=replay：优先读取预录轨迹（data/replay/embeddings.json），否则走离线确定性通道。"""

    def __init__(self, replay_dir: Path, dim: int = 1024):
        super().__init__(dim=dim)
        self.embed_model_name = f"offline-hash-{dim}"
        self._recorded_embeddings: dict[str, list[float]] = {}
        file = replay_dir / "embeddings.json"
        if file.exists():
            try:
                self._recorded_embeddings = json.loads(file.read_text(encoding="utf-8"))
                logger.info("replay: loaded %d recorded embeddings", len(self._recorded_embeddings))
            except (ValueError, OSError):
                logger.warning("replay embeddings file invalid, fallback to offline embed")

    async def embed(self, texts: list[str], *, model_alias: str = "embed") -> list[list[float]]:
        result = []
        for text in texts:
            key = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if key in self._recorded_embeddings:
                result.append(self._recorded_embeddings[key])
            else:
                result.append(self._hash_embed(text))
        return result


class ResilientGateway:
    """主通道 + 离线兜底：主失败 → L1（简化分析）；熔断打开 → L2。"""

    def __init__(self, primary, fallback: OfflineModelGateway):
        self.primary = primary
        self.fallback = fallback

    async def chat(self, messages, *, model_alias="default", temperature=0.0, json_mode=False, timeout_s=None) -> ChatResult:
        try:
            result = await self.primary.chat(
                messages, model_alias=model_alias, temperature=temperature, json_mode=json_mode, timeout_s=timeout_s
            )
            result.degrade_level = "L0"
            return result
        except LlmUnavailableError as exc:
            LLM_FAILURES.labels(alias=model_alias).inc()
            open_breaker = isinstance(getattr(self.primary, "breaker", None), pybreaker.CircuitBreaker) and self.primary.breaker.current_state == "open"
            level = "L2" if open_breaker else "L1"
            logger.warning("LLM degraded to %s: %s", level, exc)
            result = await self.fallback.chat(
                messages, model_alias=model_alias, temperature=temperature, json_mode=json_mode, timeout_s=timeout_s
            )
            result.degrade_level = level
            return result

    async def embed(self, texts: list[str], *, model_alias: str = "embed") -> list[list[float]]:
        try:
            return await self.primary.embed(texts, model_alias=model_alias)
        except LlmUnavailableError:
            LLM_FAILURES.labels(alias=model_alias).inc()
            logger.warning("embed degraded to offline hash embedder")
            return await self.fallback.embed(texts, model_alias=model_alias)

    async def rerank(self, query: str, docs: list[str], *, model_alias: str = "rerank") -> list[float]:
        try:
            return await self.primary.rerank(query, docs, model_alias=model_alias)
        except LlmUnavailableError:
            LLM_FAILURES.labels(alias=model_alias).inc()
            return await self.fallback.rerank(query, docs, model_alias=model_alias)


def build_model_gateway(settings):
    """按配置构建网关（DEMO_MODE=live 需要 LLM_BASE_URL 与 LLM_API_KEY）。"""
    offline = OfflineModelGateway(dim=settings.EMBED_DIM)
    demo_mode = (settings.DEMO_MODE or "offline").lower()
    if demo_mode == "live" and settings.LLM_BASE_URL:
        primary = OpenAiCompatGateway(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
            embed_model=settings.EMBED_MODEL,
            rerank_model=settings.RERANK_MODEL,
            timeout_s=settings.LLM_TIMEOUT_S,
        )
        return ResilientGateway(primary, offline)
    if demo_mode == "replay":
        return ReplayGateway(Path(settings.DATA_DIR) / "replay", dim=settings.EMBED_DIM)
    return offline
