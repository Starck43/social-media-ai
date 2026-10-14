"""Synthetic shell pages only: strict Jinja, no app imports, runtime or DB.

Reuses the isolated home/escaping fixtures. Empty collections are intentional:
this checks shell migration, not complete page functionality or real roles.
"""

import json
import runpy
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
home = runpy.run_path(str(ROOT / "tests/home_overview_fixtures.py"))
env, out = home["env"], home["OUT"]
detail = runpy.run_path(str(ROOT / "tests/unit_web/test_template_autoescape.py"))["context"](
    "ДлинноеНазваниеБезПробелов" * 5
)
common = {**home["base"], **detail}
common.update(
    tenant=home["base"]["tenant"],
    user=home["base"]["user"],
    csrf=home["base"]["csrf"],
    is_superuser=False,
    filter_tenant_id=None,
    perms=SimpleNamespace(
        can=lambda *args: True, can_manage_workspace=lambda target: target == 7, is_owner=True, workspace_role="MANAGER"
    ),
    perms_can=lambda *args: True,
    is_owner=True,
    messages=[],
    periods=[(7, "7 дней")],
    days=7,
    plural=lambda *args: "анализов",
    grouped_data=dict(
        visible_axes=[], axis="themes", group_counts={}, groups=[], sentiment=None, filter_return_to="/app/analytics"
    ),
    sentiment_label="Нет данных",
    last_checked=None,
    last_checked_label="Не проверено",
    job_id=None,
    pending_count=0,
    retry_needed=False,
    can_retry=False,
    retry_available=False,
    retry_running=False,
    retry_window_known=False,
    given_up=False,
    given_up_label="",
    running_job_id=None,
    running_job_status=None,
    sort="date",
    sorts=[],
    analytics_url="/app/analytics",
    axis_label="Тема",
    value="Обзор",
    items=[],
    analysis={"id": 8, "created_at": None, "source": None, "source_id": 7, "analysis_date": None},
    window_start=None,
    window_end=None,
    analyzed_at=None,
    platform_name="VK",
    chain_url=None,
    topics=[],
    vk_reactions=False,
    display=dict(
        display_title="Анализ",
        content_statistics=dict(
            total_posts=0,
            total_reactions=0,
            total_comments=0,
            active_users=0,
            avg_reactions_per_post=None,
            avg_comments_per_post=None,
            avg_views_per_post=None,
            total_views=None,
        ),
        coverage=dict(incomplete=False, reasons=[]),
        original_links=[],
        metrics_legacy={},
        analysis_summary="Мок",
        sentiment=dict(score=None),
        overall_mood="",
        highlights_available=False,
        highlights=[],
    ),
    analytics=dict(
        kpis=dict(total_analyses=0, total_posts=0, peak_users=0, cost_usd=0),
        chains=[],
        engagement=None,
        sentiment_by_user=[],
        activity_by_user=[],
        topics=[],
        entities=[],
        llm=dict(providers={}),
    ),
    selected_period=7,
    selected_group_by="source",
    selected_time_breakdown="day",
    preview_period=("7 дней", "2026-10-07", "2026-10-14"),
    preview_text="",
    runs=[],
    stats=dict(sent=0, failed=0, skipped=0, cost_usd=0, pending=0, running=0, done=0),
    jobs=[],
    signals=dict(overdue_pending=0, stale_running=0, unconfirmed_failed=0),
    job_outcomes={},
    sources_map={},
    retryable=set(),
    cancellable=set(),
    deletable=set(),
    unconfirmed_jobs=set(),
    thresholds={},
    default_start_date="2026-10-14",
    has_source=False,
    has_task=False,
    presets=[],
    llm_models=[],
    llm_strategies=[],
    create=True,
    scenario_id=None,
    analysis_types=[],
    all_analysis_types=[],
    analysis_defaults={},
    available_variables={},
    content_types=[],
    media_types=[],
    pretty_json=lambda obj: json.dumps(obj, ensure_ascii=False),
    show_preview_step=False,
    tab="team",
    visible_tabs=["team"],
    settings_manage=True,
    settings_team_visible=True,
    memberships=[],
    member_names={},
    member_role_keys={},
    role_labels={},
    sources=[],
    scenarios=[],
    tasks=[],
    members=[],
    modes=[],
    connection_banners=[],
    source_connections={},
    empty_state_cta=None,
    running_jobs={},
    unconfirmed_tasks=set(),
    denied_section="Источники",
)
scenario = dict(
    id=3,
    name="Сценарий",
    description="",
    is_active=True,
    is_default=False,
    audio_prompt="",
    image_prompt="",
    text_prompt="",
    video_prompt="",
    unified_summary_prompt="",
    text_llm_model_id=None,
    image_llm_model_id=None,
    video_llm_model_id=None,
    max_tokens=1000,
    llm_strategy=None,
)
common["chain"]["evolution"] = []
common["chains"] = []
common["scenario"] = scenario
common["draft"] = {
    **scenario,
    "content_types": [],
    "analysis_types": [],
    "scope": {},
    "base_prompt": "",
    "summary_prompt": "",
    "media_overrides": {},
}


def offline(html):
    for external, local in [
        ("https://cdn.tailwindcss.com", "tailwind.js"),
        ("https://unpkg.com/htmx.org@1.9.12", "htmx.js"),
        ("https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js", "alpine.js"),
        ("https://cdn.jsdelivr.net/npm/marked@4.3.0/marked.min.js", "marked.js"),
    ]:
        html = html.replace(external, "http://fixture.test/static/vendor/" + local)
    return html


def section_for(name):
    for prefix in ["analytics", "source", "task", "scenario"]:
        if name.startswith(prefix):
            return {"source": "sources", "task": "tasks", "scenario": "scenarios"}.get(prefix, prefix)
    return name


names = []
for path in sorted((ROOT / "app/web/templates/web").glob("*.html")):
    if '{% extends "web/base.html" %}' not in path.read_text():
        continue
    c = {**common, "section": section_for(path.stem)}
    if path.stem == "task_edit":
        c["sources"] = detail["sources"]
    if path.stem == "analytics_detail":
        c["chain"] = []
    html = env.get_template("web/" + path.name).render(**c)
    (out / (path.stem + ".html")).write_text(offline(html))
    names.append(path.stem)
# Read-only representative paths; these mock permission decisions, not authentication.
for name in ["source_detail", "task_detail", "scenario_detail", "scenarios", "settings", "chat"]:
    c = {
        **common,
        "is_owner": False,
        "can_delete": False,
        "section": section_for(name),
        "perms_can": lambda *args: args[-1] == "view",
        "perms": SimpleNamespace(
            can=lambda *args: args[-1] == "view",
            can_manage_workspace=lambda *args: False,
            is_owner=False,
            workspace_role="VIEWER",
        ),
    }
    (out / (name + "-viewer.html")).write_text(offline(env.get_template("web/" + name + ".html").render(**c)))
    names.append(name + "-viewer")
panel = env.from_string(
    '{% extends "web/base.html" %}{% block page_heading %}Контекст{% endblock %}'
    '{% block right_panel %}<p>Разрешённый мок-контекст</p><button type="button">Действие</button>{% endblock %}'
)
(out / "panel.html").write_text(offline(panel.render(**common)))
names.append("panel")
invite = {
    **common,
    "issued_invite": dict(
        role="Viewer",
        expires="2026-10-21",
        code="MOCK-CODE",
        link="http://fixture.test/app/invite/MOCK-CODE",
        qr_svg='<svg aria-label="Мок QR"></svg>',
    ),
    "invite_roles": [
        dict(key="viewer", label="Viewer", hint="Чтение"),
        dict(key="manager", label="Manager", hint="Управление"),
    ],
    "invite_default": "viewer",
    "invite_days": [1, 7, 30],
    "open_invites": [dict(id=1, role="Viewer", created="2026-10-14", expires="2026-10-21", uses=0)],
}
(out / "invite-partial.html").write_text(env.get_template("web/_invite_dialog.html").render(**invite))
(out / "pages.json").write_text(json.dumps(names))
print(f"Rendered {len(names)} additional strict synthetic fixtures; no app imports or DB.")
