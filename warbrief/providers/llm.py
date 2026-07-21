from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod

import httpx

from warbrief.config import Settings
from warbrief.models import (
    Claim,
    FactPack,
    NewsArticle,
    NewsEvent,
    ScriptManifest,
    ScriptSentence,
    SourceEvidence,
)
from warbrief.utils import clean_text, normalize_base_url

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    model_name: str

    @abstractmethod
    async def build_fact_pack(self, event: NewsEvent, articles: list[NewsArticle]) -> FactPack:
        raise NotImplementedError

    @abstractmethod
    async def build_script(
        self, event: NewsEvent, fact_pack: FactPack, max_sentences: int
    ) -> ScriptManifest:
        raise NotImplementedError


def _evidence_from_articles(articles: list[NewsArticle]) -> list[SourceEvidence]:
    output: list[SourceEvidence] = []
    for index, article in enumerate(articles, start=1):
        excerpt = clean_text(article.content or article.summary or article.title)[:1200]
        output.append(
            SourceEvidence(
                source_id=f"source_{index:02d}",
                article_id=article.id,
                title=article.title,
                url=article.url,
                published_at=article.published_at,
                quote=excerpt,
            )
        )
    return output


class MockLLMProvider(LLMProvider):
    model_name = "mock-rule-engine"

    async def build_fact_pack(self, event: NewsEvent, articles: list[NewsArticle]) -> FactPack:
        sources = _evidence_from_articles(articles)
        claims: list[Claim] = []
        for index, source in enumerate(sources[:4], start=1):
            statement = source.quote
            chunks = re.split(r"(?<=[。！？.!?])\s*", statement)
            statement = next((chunk for chunk in chunks if len(chunk) >= 12), statement)
            statement = clean_text(statement)[:220]
            if statement:
                claims.append(
                    Claim(
                        claim_id=f"claim_{index:02d}",
                        statement=statement,
                        evidence_source_ids=[source.source_id],
                        confidence=0.78 if len(sources) == 1 else 0.86,
                    )
                )
        if not claims:
            claims.append(
                Claim(
                    claim_id="claim_01",
                    statement=event.summary or event.title,
                    evidence_source_ids=[source.source_id for source in sources[:1]],
                    confidence=0.7,
                )
            )
        return FactPack(
            event_id=event.id,
            event_title=event.title,
            sources=sources,
            claims=claims,
            model=self.model_name,
        )

    async def build_script(
        self, event: NewsEvent, fact_pack: FactPack, max_sentences: int
    ) -> ScriptManifest:
        claims = fact_pack.claims or [
            Claim(claim_id="claim_01", statement=event.summary or event.title)
        ]
        content = [
            (
                f"今天值得关注的一条军事动态是：{event.title}。",
                [claims[0].claim_id],
                "military news briefing command center",
                "document",
            ),
            (
                f"根据目前公开信息，{claims[0].statement.rstrip('。')}。",
                [claims[0].claim_id],
                "military operation official footage",
                "event",
            ),
        ]
        if len(claims) > 1:
            content.append(
                (
                    f"另一份公开材料还提到，{claims[1].statement.rstrip('。')}。",
                    [claims[1].claim_id],
                    "military equipment training archive footage",
                    "archive",
                )
            )
        content.extend(
            [
                (
                    "这类消息首先要区分公开事实、机构表态和外界推测，不能把尚未证实的信息当成结论。",
                    [claims[0].claim_id],
                    "military documents radar map analysis",
                    "document",
                ),
                (
                    "从军事层面看，真正需要观察的是部署范围、参与单位、行动持续时间以及后续官方通报。",
                    [claims[0].claim_id],
                    "military map fleet aircraft deployment",
                    "map",
                ),
                (
                    "现阶段更稳妥的判断是：事件已经释放出新的动向，但其长期影响仍要等待更多独立来源确认。",
                    [claim.claim_id for claim in claims[:2]],
                    "world map defense analysis newsroom",
                    "generic",
                ),
                (
                    "以上内容仅依据当前公开资料整理，后续出现新的权威信息，我们再继续跟进。",
                    [claims[0].claim_id],
                    "military news closing world map",
                    "generic",
                ),
            ]
        )
        content = content[: max(3, max_sentences)]
        sentences = [
            ScriptSentence(
                sentence_id=f"sentence_{index:02d}",
                text=text,
                claim_ids=claim_ids,
                visual_query=query,
                visual_type=visual_type,
            )
            for index, (text, claim_ids, query, visual_type) in enumerate(content, start=1)
        ]
        return ScriptManifest(
            event_id=event.id,
            title=event.title,
            hook=sentences[0].text,
            sentences=sentences,
            closing=sentences[-1].text,
            model=self.model_name,
        )


class OpenAICompatibleLLMProvider(LLMProvider):
    def __init__(self, settings: Settings, fallback: LLMProvider | None = None):
        self.settings = settings
        self.model_name = settings.llm_model
        self.fallback = fallback or MockLLMProvider()

    async def _complete_json(self, system: str, user: str) -> dict:
        endpoint = normalize_base_url(self.settings.llm_base_url, "chat/completions")
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        body = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(
            timeout=self.settings.llm_timeout_seconds,
            proxy=self.settings.http_proxy_url or None,
        ) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            if response.status_code >= 400 and "response_format" in response.text:
                body.pop("response_format", None)
                response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        return self._parse_json(str(content))

    @staticmethod
    def _parse_json(content: str) -> dict:
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        try:
            value = json.loads(content)
            if not isinstance(value, dict):
                raise ValueError("LLM JSON root must be an object") from None
            return value
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, flags=re.S)
            if not match:
                raise
            value = json.loads(match.group(0))
            if not isinstance(value, dict):
                raise ValueError("LLM JSON root must be an object") from None
            return value

    async def build_fact_pack(self, event: NewsEvent, articles: list[NewsArticle]) -> FactPack:
        sources = _evidence_from_articles(articles)
        source_payload = [
            {
                "source_id": source.source_id,
                "title": source.title,
                "url": source.url,
                "published_at": source.published_at.isoformat(),
                "excerpt": source.quote,
            }
            for source in sources
        ]
        system = (
            "你是军事新闻事实编辑。只抽取输入材料直接支持的事实，不补充常识，不推断未公开参数。"
            "对存在冲突或仅单一来源支持的内容降低置信度。必须输出合法 JSON。"
        )
        user = json.dumps(
            {
                "task": "构建事实包",
                "event_title": event.title,
                "sources": source_payload,
                "output_schema": {
                    "claims": [
                        {
                            "statement": "可直接用于口播的事实陈述",
                            "evidence_source_ids": ["source_01"],
                            "confidence": 0.0,
                            "disputed": False,
                        }
                    ]
                },
            },
            ensure_ascii=False,
        )
        try:
            payload = await self._complete_json(system, user)
            valid_source_ids = {source.source_id for source in sources}
            claims: list[Claim] = []
            for index, item in enumerate(payload.get("claims", []), start=1):
                evidence_ids = [
                    source_id
                    for source_id in item.get("evidence_source_ids", [])
                    if source_id in valid_source_ids
                ]
                statement = clean_text(str(item.get("statement", "")))
                if not statement or not evidence_ids:
                    continue
                claims.append(
                    Claim(
                        claim_id=f"claim_{index:02d}",
                        statement=statement,
                        evidence_source_ids=evidence_ids,
                        confidence=max(0.0, min(float(item.get("confidence", 0.7)), 1.0)),
                        disputed=bool(item.get("disputed", False)),
                    )
                )
            if not claims:
                raise ValueError("LLM returned no evidence-backed claims")
            return FactPack(
                event_id=event.id,
                event_title=event.title,
                sources=sources,
                claims=claims,
                model=self.model_name,
            )
        except Exception as exc:
            logger.exception("Fact-pack generation failed; falling back: %s", exc)
            return await self.fallback.build_fact_pack(event, articles)

    async def build_script(
        self, event: NewsEvent, fact_pack: FactPack, max_sentences: int
    ) -> ScriptManifest:
        system = (
            "你是中文军事资讯短视频主编。文稿必须克制、准确、节奏清晰，不煽动，不虚构现场。"
            "每句话必须引用提供的 claim_id。画面检索词 visual_query 使用简洁英文。"
            "必须输出合法 JSON。"
        )
        user = json.dumps(
            {
                "task": "生成60至90秒竖屏军事资讯口播稿",
                "event_title": event.title,
                "claims": [claim.model_dump(mode="json") for claim in fact_pack.claims],
                "constraints": {
                    "max_sentences": max_sentences,
                    "sentence_length_chinese_chars": "18-45",
                    "no_unverified_claims": True,
                    "archive_footage_must_not_be_presented_as_live": True,
                },
                "output_schema": {
                    "title": "视频标题",
                    "hook": "开头钩子",
                    "sentences": [
                        {
                            "text": "口播句子",
                            "claim_ids": ["claim_01"],
                            "visual_query": "English media search query",
                            "visual_type": "event|archive|map|document|generic",
                        }
                    ],
                    "closing": "结尾",
                },
            },
            ensure_ascii=False,
        )
        try:
            payload = await self._complete_json(system, user)
            valid_claim_ids = {claim.claim_id for claim in fact_pack.claims}
            sentences: list[ScriptSentence] = []
            for index, item in enumerate(payload.get("sentences", [])[:max_sentences], start=1):
                text = clean_text(str(item.get("text", "")))
                claim_ids = [
                    claim_id
                    for claim_id in item.get("claim_ids", [])
                    if claim_id in valid_claim_ids
                ]
                if not text or not claim_ids:
                    continue
                visual_type = str(item.get("visual_type", "generic"))
                if visual_type not in {"event", "archive", "map", "document", "generic"}:
                    visual_type = "generic"
                sentences.append(
                    ScriptSentence(
                        sentence_id=f"sentence_{index:02d}",
                        text=text,
                        claim_ids=claim_ids,
                        visual_query=clean_text(str(item.get("visual_query", "military news")))
                        or "military news",
                        visual_type=visual_type,
                    )
                )
            if len(sentences) < 3:
                raise ValueError("LLM returned too few grounded sentences")
            return ScriptManifest(
                event_id=event.id,
                title=clean_text(str(payload.get("title", event.title))) or event.title,
                hook=clean_text(str(payload.get("hook", sentences[0].text))),
                sentences=sentences,
                closing=clean_text(str(payload.get("closing", sentences[-1].text))),
                model=self.model_name,
            )
        except Exception as exc:
            logger.exception("Script generation failed; falling back: %s", exc)
            return await self.fallback.build_script(event, fact_pack, max_sentences)


def create_llm_provider(settings: Settings) -> LLMProvider:
    if settings.use_real_llm:
        return OpenAICompatibleLLMProvider(settings)
    return MockLLMProvider()
