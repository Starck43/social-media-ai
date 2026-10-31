"""P8-T43: Preserve post permalinks and media URL references through analysis."""

from __future__ import annotations

import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

from app.agent.prompts import TASK_SECTION
from app.core.permissions import permission_scope
from app.core.tenant_context import current_tenant_id, tenant_scope
from app.models.collection.collected_item import CollectedItem
from app.services.ai.analysis_render import extract_post_evidence, render_analysis, safe_media_url, safe_original_url
from app.services.ai.analysis_stats import AnalysisStatsMixin
from app.services.ai.content_classifier import ContentClassifier
from app.services.collection.adapters.vk import VKCollectionAdapter
from app.services.monitoring.collector import _attach_parent_context
from app.services.monitoring.ingest import normalize_channel_post
from app.services.social.tg_client import TelegramClient
from app.services.social.vk_client import VKClient
from app.services.task_results import task_result
from app.types import ActionType
from app.utils.collected_content import build_staged_rows

__source_only__ = True

ROOT_DIR = Path(__file__).resolve().parents[2]
SAFE_IMAGE_URL = "https://sun9-42.userapi.com/impg/photo_1080.jpg"
SAFE_IMAGE_URL_2 = "https://sun9-42.userapi.com/impg/photo_720.jpg"
SAFE_VIDEO_URL = "https://cdn.example.org/media/clip_1080.mp4"


def _render_detail_html(summary_data: dict) -> str:
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {"web/base.html": "{% block page_actions %}{% endblock %}{% block content %}{% endblock %}"}
                ),
                FileSystemLoader(ROOT_DIR / "app/web/templates"),
            ]
        ),
        autoescape=select_autoescape(("html",)),
    )
    env.filters["human_dt"] = lambda value, **_kwargs: str(value or "")
    env.filters["ui_label"] = str
    return env.get_template("web/analytics_detail.html").render(
        analysis=SimpleNamespace(id=501, source_id=37, analysis_date="2026-10-30", llm_model="gemini-2.5-flash"),
        display=render_analysis(summary_data),
        retry={"task_links": [{"url": "/app/tasks/12", "name": "Research task"}], "blocking": []},
        back_url="/app/tasks/12",
        back_label="К задаче",
        chain_url=None,
        platform_name="VK",
        window_start="2026-10-30",
        analyzed_at="2026-10-30",
        topics=[],
        vk_reactions=False,
    )


def test_vk_client_and_collection_adapter_preserve_post_and_comment_media_links():
    client = VKClient.__new__(VKClient)
    raw_response = {
        "response": {
            "items": [
                {
                    "id": 115,
                    "owner_id": -77,
                    "date": 1730000000,
                    "text": "Обзор новой коллекции керамогранита в шоуруме",
                    "likes": {"count": 12},
                    "comments": {"count": 4},
                    "reposts": {"count": 2},
                    "views": {"count": 340},
                    "attachments": [
                        {
                            "type": "photo",
                            "photo": {
                                "sizes": [
                                    {"url": SAFE_IMAGE_URL_2, "width": 600, "height": 400},
                                    {"url": SAFE_IMAGE_URL, "width": 1200, "height": 800},
                                ]
                            },
                        },
                        {
                            "type": "video",
                            "video": {"image": [{"url": SAFE_IMAGE_URL_2}], "player": "https://vk.com/video-77_115"},
                        },
                        {"type": "doc", "doc": {"url": "https://vk.com/doc-77_1"}},
                    ],
                }
            ]
        }
    }
    normalized = client._normalize_response(raw_response, "group")[0]
    adapter_item = VKCollectionAdapter._item(normalized)

    assert adapter_item["external_id"] == "-77_115"
    assert adapter_item["permalink"] == "https://vk.com/wall-77_115"
    assert adapter_item["attachments"] == [
        {"type": "image", "url": SAFE_IMAGE_URL},
        {"type": "video", "url": None},
        {"type": "unknown", "url": None},
    ]
    assert adapter_item["has_attachments"] is True
    assert adapter_item["attachment_types"] == ["photo", "video", "doc"]

    raw_comment = {
        "id": 901,
        "from_id": 42,
        "date": 1730000100,
        "text": "Подскажите цену за квадратный метр, пожалуйста",
        "likes": {"count": 3},
        "attachments": [
            {"type": "photo", "photo": {"sizes": [{"url": SAFE_IMAGE_URL_2, "width": 800, "height": 600}]}},
        ],
    }
    comment_item = VKCollectionAdapter._comment(
        raw_comment,
        owner_id=-77,
        post_id=115,
        post_external_id="-77_115",
        profiles={42: {"id": 42, "first_name": "Анна", "last_name": "Смирнова"}},
    )
    assert comment_item["external_id"] == "-77_115:901"
    assert comment_item["parent_external_id"] == "-77_115"
    assert comment_item["permalink"] == "https://vk.com/wall-77_115?reply=901"
    assert comment_item["attachments"] == [{"type": "image", "url": SAFE_IMAGE_URL_2}]


def test_telegram_ingest_mtproto_and_staging_permalink_and_media_placeholders():
    inbound = SimpleNamespace(
        is_channel_post=True,
        chat_id="-100998877",
        channel="telegram",
        raw={
            "channel_post": {
                "message_id": 58,
                "date": 1730000000,
                "caption": "Фотоотчёт со стройплощадки жилого комплекса",
                "views": 120,
                "chat": {"id": -100998877, "username": "kirov_design_club", "type": "channel"},
                "photo": [{"file_id": "SECRET_FILE_ID"}],
            }
        },
    )
    ingested = normalize_channel_post(inbound)
    assert ingested is not None
    assert ingested["external_id"] == "-100998877_58"
    assert ingested["permalink"] == "https://t.me/kirov_design_club/58"
    assert ingested["attachments"] == [{"type": "image", "url": None}]
    assert "SECRET_FILE_ID" not in repr(ingested)

    tg_client = TelegramClient.__new__(TelegramClient)
    public_source = SimpleNamespace(
        id=19,
        tenant_id=3,
        external_id="@kirov_design_club",
        source_type="channel",
        platform=SimpleNamespace(code="telegram"),
        params={"platform": "telegram"},
    )
    msg = SimpleNamespace(
        id=64,
        text="Видеообзор готового интерьера квартиры",
        message="Видеообзор готового интерьера квартиры",
        media=type("MessageMediaDocument", (), {"document": SimpleNamespace(mime_type="video/mp4", attributes=[])})(),
        chat_id=-100998877,
        date=datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc),
        views=85,
        forwards=4,
        reactions=None,
        replies=None,
        is_channel=True,
        sender_id=None,
        pinned=False,
        edit_date=None,
    )
    pulled = tg_client._normalize_message(msg, public_source)
    assert pulled is not None
    staged_public = build_staged_rows([pulled], public_source, run_id=77)[0]
    assert staged_public["permalink"] == "https://t.me/kirov_design_club/64"
    assert staged_public["attachments"] == [{"type": "video", "url": None}]

    private_source = SimpleNamespace(
        id=20,
        tenant_id=3,
        external_id="-100998877",
        source_type="channel",
        platform=SimpleNamespace(code="telegram"),
        params={"platform": "telegram"},
    )
    pulled_private = tg_client._normalize_message(msg, private_source)
    staged_private = build_staged_rows([pulled_private], private_source, run_id=78)[0]
    assert staged_private["permalink"] is None


def test_staging_and_replay_preserve_source_identity_tenant_scope_and_parent_links():
    vk_source = SimpleNamespace(
        id=37,
        tenant_id=5,
        external_id="-77",
        platform=SimpleNamespace(code="vk"),
        params={"platform": "vk", "owner_id": "-77"},
    )
    parent_post = {
        "external_id": "-77_115",
        "platform": "vk",
        "content_type": "post",
        "text": "Открыта запись на консультацию по комплектации интерьера",
        "published_at": "2026-10-30T10:00:00Z",
        "attachments": [{"type": "photo", "url": SAFE_IMAGE_URL}],
    }
    comment = {
        "external_id": "-77_115:901",
        "parent_external_id": "-77_115",
        "platform": "vk",
        "content_type": "comment",
        "text": "Хочу записаться на четверг!",
        "published_at": "2026-10-30T10:15:00Z",
        "permalink": "https://vk.com/wall-77_115?reply=901",
    }
    staged_parent = build_staged_rows([parent_post], vk_source, run_id=101)[0]
    assert staged_parent["permalink"] == "https://vk.com/wall-77_115"
    _attach_parent_context([parent_post], [comment])
    staged_comment = build_staged_rows([comment], vk_source, run_id=101)[0]

    replayed_post = CollectedItem(
        id=1,
        tenant_id=5,
        source_id=37,
        external_id=staged_parent["external_id"],
        content_hash=staged_parent["content_hash"],
        content_type=staged_parent["content_type"],
        parent_external_id=staged_parent["parent_external_id"],
        lane_id=None,
        platform=staged_parent["platform"],
        published_at=staged_parent["published_at"],
        media_type=staged_parent["media_type"],
        attachments=staged_parent["attachments"],
        text=staged_parent["text"],
        metrics=staged_parent["metrics"],
        author=staged_parent["author"],
        permalink=staged_parent["permalink"],
    ).as_agent_item()

    replayed_comment = CollectedItem(
        id=2,
        tenant_id=5,
        source_id=37,
        external_id=staged_comment["external_id"],
        content_hash=staged_comment["content_hash"],
        content_type=staged_comment["content_type"],
        parent_external_id=staged_comment["parent_external_id"],
        lane_id=None,
        platform=staged_comment["platform"],
        published_at=staged_comment["published_at"],
        media_type=staged_comment["media_type"],
        attachments=staged_comment["attachments"],
        text=staged_comment["text"],
        metrics=staged_comment["metrics"],
        author=staged_comment["author"],
        permalink=staged_comment["permalink"],
    ).as_agent_item()

    assert replayed_post["source_id"] == 37
    assert replayed_post["tenant_id"] == 5
    assert replayed_post["permalink"] == "https://vk.com/wall-77_115"
    assert replayed_post["attachments"] == [{"type": "image", "url": SAFE_IMAGE_URL}]

    assert replayed_comment["permalink"] == "https://vk.com/wall-77_115?reply=901"
    assert replayed_comment["parent_context"]["permalink"] == "https://vk.com/wall-77_115"

    prompt_context = ContentClassifier.prepare_text_content([replayed_post, replayed_comment])
    assert f"link=https://vk.com/wall-77_115 | media={SAFE_IMAGE_URL}" in prompt_context
    assert "[CONTEXT: parent vk:-77_115 2026-10-30T10:00:00Z link=https://vk.com/wall-77_115]" in prompt_context
    assert "link=https://vk.com/wall-77_115?reply=901" in prompt_context


def test_missing_malformed_duplicate_expired_and_unsafe_urls_do_not_fabricate_or_block_text():
    stats_helper = AnalysisStatsMixin()
    items = [
        {
            "external_id": "post_1",
            "platform": "vk",
            "source_id": 37,
            "tenant_id": 5,
            "date": "2026-10-30",
            "text": "Полезный пост с дубликатами фото и протухшей ссылкой на второе фото",
            "permalink": "https://vk.com/wall-77_1",
            "attachments": [
                {"type": "image", "url": SAFE_IMAGE_URL},
                {"type": "photo", "url": SAFE_IMAGE_URL},  # duplicate URL on same post
                {"type": "image", "url": f"{SAFE_IMAGE_URL}?sig=expired_token"},  # signed/query URL rejected
                {"type": "video", "url": None},  # missing video URL
                {"type": "doc", "url": "https://vk.com/doc1"},  # unsupported attachment type
            ],
        },
        {
            "external_id": "post_2",
            "platform": "vk",
            "source_id": 37,
            "tenant_id": 5,
            "date": "2026-10-30",
            "text": "Второй полезный пост с вредоносным permalink, который должен быть отброшен",
            "permalink": "javascript:alert(document.cookie)",
            "attachments": [
                {"type": "image", "url": "https://api.telegram.org/file/botSECRET/photo.jpg"},
                {"type": "image", "url": "http://localhost:8080/internal.jpg"},
                {"type": "image", "url": "https://127.0.0.1/loopback.jpg"},
                {"type": "image", "url": "https://user:secret@cdn.example.org/private.jpg"},
                {"type": "video", "url": SAFE_VIDEO_URL},
            ],
        },
    ]

    classified = ContentClassifier.classify_content(items)
    assert len(classified["text"]) == 2, "Malformed/missing URLs must never drop usable post text"

    stats = stats_helper._calculate_content_stats(items)
    assert stats["original_links"] == ["https://vk.com/wall-77_1"]
    evidence = stats["post_evidence"]
    assert len(evidence) == 2

    first = evidence[0]
    assert first["external_id"] == "post_1"
    assert first["permalink"] == "https://vk.com/wall-77_1"
    assert first["source_id"] == 37
    assert first["tenant_id"] == 5
    assert first["media_urls"] == [SAFE_IMAGE_URL]
    assert [(a["type"], a["url"], a["status"]) for a in first["attachments"]] == [
        ("image", SAFE_IMAGE_URL, "available"),
        ("image", None, "missing_media_url"),
        ("video", None, "missing_media_url"),
        ("unknown", None, "unsupported_attachment"),
    ]

    second = evidence[1]
    assert second["external_id"] == "post_2"
    assert second["permalink"] is None
    assert second["media_urls"] == [SAFE_VIDEO_URL]
    assert ("video", SAFE_VIDEO_URL, "available") in [(a["type"], a["url"], a["status"]) for a in second["attachments"]]
    assert "javascript:" not in repr(stats)
    assert "botSECRET" not in repr(stats)
    assert "secret@" not in repr(stats)


def test_reference_only_invariant_never_invokes_network_download_or_file_storage():
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("Media download, proxying, or byte persistence must never be invoked")

    source = SimpleNamespace(
        id=37,
        tenant_id=5,
        external_id="-77",
        platform=SimpleNamespace(code="vk"),
        params={"platform": "vk"},
    )
    raw_vk = {
        "response": {
            "items": [
                {
                    "id": 200,
                    "owner_id": -77,
                    "date": 1730000000,
                    "text": "Пост с изображением только по ссылке без скачивания байтов",
                    "attachments": [
                        {"type": "photo", "photo": {"sizes": [{"url": SAFE_IMAGE_URL, "width": 1000, "height": 800}]}}
                    ],
                }
            ]
        }
    }

    with (
        patch.object(httpx.AsyncClient, "get", side_effect=_forbidden),
        patch.object(httpx.AsyncClient, "request", side_effect=_forbidden),
        patch.object(httpx.Client, "get", side_effect=_forbidden),
        patch.object(urllib.request, "urlopen", side_effect=_forbidden),
    ):
        vk_client = VKClient.__new__(VKClient)
        normalized = VKCollectionAdapter._item(vk_client._normalize_response(raw_vk, "group")[0])
        staged = build_staged_rows([normalized], source, run_id=301)[0]
        replayed = CollectedItem(
            id=10,
            tenant_id=5,
            source_id=37,
            external_id=staged["external_id"],
            content_hash=staged["content_hash"],
            content_type=staged["content_type"],
            parent_external_id=None,
            lane_id=None,
            platform=staged["platform"],
            published_at=staged["published_at"],
            media_type=staged["media_type"],
            attachments=staged["attachments"],
            text=staged["text"],
            metrics=staged["metrics"],
            author=staged["author"],
            permalink=staged["permalink"],
        ).as_agent_item()
        prompt = ContentClassifier.prepare_text_content([replayed])
        stats = AnalysisStatsMixin()._calculate_content_stats([replayed])
        rendered = render_analysis({"content_statistics": stats})

    assert SAFE_IMAGE_URL in prompt
    assert rendered["original_links"] == ["https://vk.com/wall-77_200"]
    assert rendered["post_evidence"][0]["media_urls"] == [SAFE_IMAGE_URL]


async def test_agent_task_result_serializes_post_and_media_links_with_cross_tenant_isolation(monkeypatch):
    import app.models

    assert "post_evidence" in TASK_SECTION and "permalink" in TASK_SECTION

    tenant_rows = {
        2: [
            SimpleNamespace(
                id=10,
                source_id=37,
                summary_data={
                    "analysis_title": "Обзор шоурума",
                    "analysis_summary": "Высок интерес к крупноформатному керамограниту.",
                    "source_metadata": {"source_id": 37, "tenant_id": 2, "platform": "VK", "source_name": "Шоурум"},
                    "content_statistics": {
                        "original_links": ["https://vk.com/wall-77_115"],
                        "post_evidence": [
                            {
                                "external_id": "-77_115",
                                "content_type": "post",
                                "permalink": "https://vk.com/wall-77_115",
                                "source_id": 37,
                                "tenant_id": 2,
                                "attachments": [
                                    {"type": "image", "url": SAFE_IMAGE_URL},
                                    {"type": "video", "url": None},
                                ],
                            }
                        ],
                    },
                },
            )
        ],
        9: [],  # Tenant 9 has no access to tenant 2's AIAnalytics rows
    }

    class ScopedQuery:
        def __init__(self, rows):
            self.rows = list(rows)

        def filter(self, **_kwargs):
            return self

        def order_by(self, *_args):
            return self

        def limit(self, value):
            self.rows = self.rows[:value]
            return self

        async def first(self):
            return self.rows[0] if self.rows else None

        def __await__(self):
            async def _rows():
                return self.rows

            return _rows().__await__()

    task_obj = SimpleNamespace(
        id=4,
        launch_job_id=9,
        last_status="ok",
        name="VK Monitoring",
        job_type="analyze",
        is_active=True,
        cron_expr="@once",
        payload={},
    )
    job_obj = SimpleNamespace(
        id=9,
        status="done",
        attempts=1,
        result={"items": 1, "analyzed": 1, "per_source": [{"source_id": 37, "analytics_ids": [10]}]},
    )
    field = SimpleNamespace(desc=lambda: "id desc")
    monkeypatch.setattr(
        app.models,
        "AgentTask",
        SimpleNamespace(
            objects=SimpleNamespace(
                get=AsyncMock(side_effect=lambda id: task_obj if current_tenant_id() == 2 else None)
            )
        ),
    )
    monkeypatch.setattr(
        app.models,
        "Job",
        SimpleNamespace(
            id=field,
            objects=SimpleNamespace(filter=lambda **_kw: ScopedQuery([job_obj] if current_tenant_id() == 2 else [])),
        ),
    )
    monkeypatch.setattr(
        app.models,
        "AIAnalytics",
        SimpleNamespace(
            id=field,
            objects=SimpleNamespace(filter=lambda **_kw: ScopedQuery(tenant_rows.get(current_tenant_id(), []))),
        ),
    )

    user = SimpleNamespace(id=7, is_active=True)
    full_grants = frozenset((name, ActionType.VIEW) for name in ("agenttask", "source", "aianalytics"))

    with tenant_scope(2), permission_scope(user, member_grants=full_grants):
        res_t2 = await task_result(4, limit=5)

    assert res_t2 is not None
    assert len(res_t2["analyses"]) == 1
    analysis_entry = res_t2["analyses"][0]
    assert analysis_entry["id"] == 10
    assert analysis_entry["summary"] == "Высок интерес к крупноформатному керамограниту."
    assert analysis_entry["original_links"] == ["https://vk.com/wall-77_115"]
    assert analysis_entry["post_evidence"] == [
        {
            "external_id": "-77_115",
            "content_type": "post",
            "permalink": "https://vk.com/wall-77_115",
            "media_urls": [SAFE_IMAGE_URL],
            "attachments": [
                {"type": "image", "url": SAFE_IMAGE_URL, "status": "available"},
                {"type": "video", "url": None, "status": "missing_media_url"},
            ],
            "source_id": 37,
        }
    ]

    # Foreign tenant 9 cannot read tenant 2's task or evidence links
    with tenant_scope(9), permission_scope(user, member_grants=full_grants):
        res_t9 = await task_result(4, limit=5)
    assert res_t9 is None


def test_web_analytics_detail_renders_clickable_permalinks_media_previews_and_safe_fallbacks():
    summary_data = {
        "analysis_title": "Анализ публикаций группы",
        "analysis_summary": "Обсуждаются интерьеры и материалы.",
        "content_statistics": {
            "total_posts": 2,
            "original_links": ["https://vk.com/wall-77_115", "javascript:alert(1)"],
            "post_evidence": [
                {
                    "external_id": "-77_115",
                    "content_type": "post",
                    "permalink": "https://vk.com/wall-77_115",
                    "attachments": [
                        {"type": "image", "url": SAFE_IMAGE_URL},
                        {"type": "video", "url": SAFE_VIDEO_URL},
                        {"type": "video", "url": None},
                        {"type": "unknown", "url": None},
                    ],
                },
                {
                    "external_id": "-77_115:901",
                    "content_type": "comment",
                    "permalink": "https://vk.com/wall-77_115?reply=901",
                    "parent_permalink": "https://vk.com/wall-77_115",
                    "attachments": [{"type": "image", "url": "javascript:alert(2)"}],
                },
            ],
        },
    }

    html = _render_detail_html(summary_data)
    assert "Проанализированные материалы и медиа" in html
    assert 'href="https://vk.com/wall-77_115"' in html
    assert 'href="https://vk.com/wall-77_115?reply=901"' in html
    assert "Исходный пост ↗" in html
    assert f'<img src="{SAFE_IMAGE_URL}"' in html
    assert "Открыть изображение ↗" in html
    assert f'href="{SAFE_VIDEO_URL}"' in html
    assert "Открыть видео ↗" in html
    assert "Видео без прямой ссылки" in html
    assert "Изображение без прямой ссылки" in html
    assert "Неподдерживаемое вложение" in html
    assert "javascript:" not in html


async def test_analyzer_save_persists_source_identity_tenant_scope_and_merges_daily_evidence(monkeypatch):
    import app.core.database
    import app.models
    import app.services.ai.analyzer as analyzer_mod
    from app.services.ai.analyzer import AIAnalyzer

    analyzer = AIAnalyzer()
    analyzer._price_usage = AsyncMock(return_value=None)
    analyzer._journal_paid_attempts = AsyncMock(return_value=None)
    source = SimpleNamespace(id=37, tenant_id=5, name="Клуб дизайнеров", source_type="group")

    batch_1 = [
        {
            "external_id": "-77_101",
            "platform": "vk",
            "source_id": 37,
            "tenant_id": 5,
            "date": "2026-10-30",
            "text": "Первый пост за день с фотографией",
            "permalink": "https://vk.com/wall-77_101",
            "attachments": [{"type": "image", "url": SAFE_IMAGE_URL}],
        }
    ]
    batch_2 = [
        {
            "external_id": "-77_102",
            "platform": "vk",
            "source_id": 37,
            "tenant_id": 5,
            "date": "2026-10-30",
            "text": "Второй пост за день с видео по ссылке",
            "permalink": "https://vk.com/wall-77_102",
            "attachments": [{"type": "video", "url": SAFE_VIDEO_URL}],
        }
    ]

    stats_1 = analyzer._calculate_content_stats(batch_1)
    stats_2 = analyzer._calculate_content_stats(batch_2)
    analysis_results = {
        "text_analysis": {
            "request": {"model": "gemini-2.5-flash", "provider": "google", "prompt": "p"},
            "response": {"usage": {"prompt_tokens": 10, "completion_tokens": 5}},
            "parsed": {"analysis_title": "Итоги дня", "analysis_summary": "Два важных материала."},
        }
    }

    saved_rows: list[SimpleNamespace] = []

    class _Tx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    fake_session = SimpleNamespace(begin=lambda: _Tx(), close=AsyncMock())
    monkeypatch.setattr(app.core.database, "new_session", lambda: fake_session)

    async def _create(**kwargs):
        row = SimpleNamespace(
            id=701,
            topic_chain_id=kwargs.get("topic_chain_id"),
            chain_label=kwargs.get("chain_label"),
            normalized_label=kwargs.get("normalized_label"),
            content_hash=kwargs.get("content_hash"),
            summary_data=kwargs["summary_data"],
        )
        saved_rows.append(row)
        return row

    async def _update_by_id(_row_id, **kwargs):
        saved_rows[0].summary_data = kwargs["summary_data"]
        saved_rows[0].content_hash = kwargs.get("content_hash")
        return saved_rows[0]

    fake_ai_analytics = SimpleNamespace(
        objects=SimpleNamespace(
            filter=lambda **_kw: SimpleNamespace(
                first=AsyncMock(side_effect=lambda: saved_rows[0] if saved_rows else None)
            ),
            create=AsyncMock(side_effect=_create),
            update_by_id=AsyncMock(side_effect=_update_by_id),
        )
    )
    monkeypatch.setattr(app.models, "AIAnalytics", fake_ai_analytics)
    monkeypatch.setattr(analyzer_mod, "AIAnalytics", fake_ai_analytics)
    monkeypatch.setattr(
        app.models,
        "AnalyzedContentReceipt",
        SimpleNamespace(objects=SimpleNamespace(record_successes=AsyncMock(return_value=None))),
    )

    row_1 = await analyzer._save_analysis(
        analysis_results,
        None,
        source,
        stats_1,
        "VK",
        None,
        "chain-1",
        "Интерьеры",
        content_hash="h1",
        content_hashes=["hash_101"],
    )
    assert row_1.summary_data["source_metadata"]["source_id"] == 37
    assert row_1.summary_data["source_metadata"]["tenant_id"] == 5
    assert row_1.summary_data["content_statistics"]["original_links"] == ["https://vk.com/wall-77_101"]
    assert len(row_1.summary_data["content_statistics"]["post_evidence"]) == 1

    row_2 = await analyzer._save_analysis(
        analysis_results,
        None,
        source,
        stats_2,
        "VK",
        None,
        "chain-1",
        "Интерьеры",
        content_hash="h2",
        content_hashes=["hash_102"],
    )
    assert row_2.summary_data["content_hashes"] == ["hash_101", "hash_102"]
    assert row_2.summary_data["content_statistics"]["original_links"] == [
        "https://vk.com/wall-77_101",
        "https://vk.com/wall-77_102",
    ]
    merged_evidence = row_2.summary_data["content_statistics"]["post_evidence"]
    assert [e["external_id"] for e in merged_evidence] == ["-77_101", "-77_102"]
    assert merged_evidence[0]["media_urls"] == [SAFE_IMAGE_URL]
    assert merged_evidence[1]["media_urls"] == [SAFE_VIDEO_URL]
