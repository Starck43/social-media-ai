"""Shared SVG/components and three-state theme behavior, with no new dependencies."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from starlette.requests import Request

from app.web.deps import templates


def test_all_web_templates_compile_and_use_shared_components():
    for path in Path("app/web/templates/web").glob("*.html"):
        templates.env.get_template("web/" + path.name)
    analytics = Path("app/web/templates/web/analytics.html").read_text()
    assert "sent:" not in analytics
    for emoji in ("📅", "🧩", "🔗", "👤", "🎯"):
        assert emoji not in analytics
    assert "sentiment_filters(request, gd.sentiment, gd.filter_return_to)" in analytics
    assert 'x-data="{ axis:' not in analytics  # active tabs work before/without Alpine


def test_sentiment_badges_include_words_scale_and_shared_thresholds():
    macros = templates.env.get_template("web/_macros.html").module
    for score, label in ((0.2, "Негативная"), (0.4, "Нейтральная"), (0.6, "Нейтральная"), (0.8, "Позитивная")):
        html = str(macros.sentiment_badge(score, average=True, mode="full"))
        assert label in html and f"{score:.2f} / 1" in html and "Средняя тональность" in html
    compact = str(macros.sentiment_badge(0.8))
    assert 'class="ui-tone ui-positive"' in compact and '>0.80<' in compact
    assert ' / 1</span>' not in compact
    dot = str(macros.sentiment_badge(0.8, mode="dot"))
    assert 'ui-dot' in dot and '>0.80<' not in dot and 'aria-label=' in dot
    assert not str(macros.sentiment_badge(None)).strip()


def test_filter_links_preserve_scope_origin_and_clear_only_requested_dimension():
    request = Request(
        {
            "type": "http",
            "scheme": "http",
            "server": ("localhost", 80),
            "path": "/app/analytics/group",
            "query_string": b"axis=entities&value=SETUS+Design&entity_type=brand&days=all&source_id=42&sentiment=negative&media=image&return_to=%2Fapp%2Fanalytics",
            "headers": [],
        }
    )
    html = str(
        templates.env.get_template("web/_macros.html").module.sentiment_filters(request, "negative", "/app/analytics")
    )
    assert "<select" not in html
    assert 'aria-current="page"' in html and "Сбросить фильтр медиа из ссылки" in html
    from html import unescape
    from urllib.parse import parse_qs, urlparse
    import re

    links = [parse_qs(urlparse(unescape(url)).query) for url in re.findall(r'href="([^"]+)"', html)]
    assert len(links) == 5
    for query in links:
        assert query["axis"] == ["entities"] and query["entity_type"] == ["brand"]
        assert query["days"] == ["all"] and query["source_id"] == ["42"]
        assert query["value"] == ["SETUS Design"] and query["return_to"] == ["/app/analytics"]
    assert "sentiment" not in links[0] and links[0]["media"] == ["image"]
    assert "media" not in links[-1] and links[-1]["sentiment"] == ["negative"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node optional for theme controller unit test")
def test_theme_controller_tracks_system_storage_and_blocked_persistence():
    script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const code = fs.readFileSync('app/static/js/theme.js', 'utf8');
function boot(stored, systemDark, denied=false) {
    const events={}, winEvents={}, classes=new Set(), writes=[];
    const root={classList:{toggle:(key,on)=>on?classes.add(key):classes.delete(key)},dataset:{},style:{}};
    const surface={dataset:{}}, select={value:''};
    const media={matches:systemDark,addEventListener:(_,fn)=>media.change=fn};
    const storage={getItem:()=>{if(denied)throw Error('denied');return stored;},setItem:(_,v)=>{if(denied)throw Error('denied');stored=v;writes.push(v);}};
    const document={documentElement:root,querySelectorAll:(s)=>s==='[data-ui-theme-surface]'?[surface]:s==='[data-theme-select]'?[select]:[],addEventListener:(k,fn)=>events[k]=fn};
    const window={matchMedia:()=>media,addEventListener:(k,fn)=>winEvents[k]=fn};
    vm.runInNewContext(code,{document,window,localStorage:storage});
    return {theme:window.AppTheme,root,surface,select,media,events,winEvents,writes,classes,setStored:v=>stored=v};
}
let t=boot(null,true);
assert.equal(t.theme.mode,'system');assert.equal(t.root.style.colorScheme,'dark');assert.deepEqual(t.writes,[]);
t.media.matches=false;t.media.change();assert.equal(t.root.style.colorScheme,'light');assert.equal(t.select.value,'system');
t.events.DOMContentLoaded();assert.deepEqual(t.writes,[]);
t.events.change({target:{matches:()=>true,value:'dark'}});assert.equal(t.theme.mode,'dark');assert.equal(t.surface.dataset.theme,'dark');
t.media.matches=false;t.media.change();assert(t.classes.has('dark'));assert.deepEqual(t.writes,['dark']);
t.theme.setMode('light');assert(!t.classes.has('dark'));
t.theme.setMode('system');t.media.matches=true;t.media.change();assert(t.classes.has('dark'));
assert.equal(t.writes.at(-1),'system');t.theme.setMode('invalid');assert.equal(t.theme.mode,'system');
t.setStored('light');t.winEvents.storage({key:'theme'});assert.equal(t.theme.mode,'light');assert(!t.classes.has('dark'));
t.setStored(null);t.winEvents.storage({key:null});assert.equal(t.theme.mode,'system');assert(t.classes.has('dark'));
for (const mode of ['dark','light','system']) {
    const q=boot(mode,true);assert.equal(q.theme.mode,mode);assert.equal(q.root.style.colorScheme,mode==='light'?'light':'dark');
}
const blocked=boot(null,false,true);blocked.theme.setMode('dark');assert(blocked.classes.has('dark'));
console.log('Theme modes, OS changes, cross-tab changes and denied storage passed');
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_flash_status_has_contrasting_text_in_both_themes():
    template = Path("app/web/templates/web/_flashes.html").read_text()
    assert "text-emerald-800 dark:text-emerald-200" in template
    assert "text-rose-800 dark:text-rose-200" in template


def test_auth_and_app_share_theme_controller_without_duplicate_flashes():
    for name in ("base.html", "auth_base.html"):
        template = Path("app/web/templates/web", name).read_text()
        assert "js/theme.js" in template and "data-theme-select" in template
        assert "themeManager()" not in template and "localStorage" not in template
        for mode in ("light", "dark", "system"):
            assert f'value="{mode}"' in template
    scenarios = Path("app/web/templates/web/scenarios.html").read_text()
    assert "for flash in flashes" not in scenarios


def test_native_choice_labels_are_plain_and_settings_use_shared_segments():
    from app.web.deps import human_choice_label

    assert human_choice_label("📋 Дайджест") == "Дайджест"
    assert human_choice_label("🔄 Сбор данных") == "Сбор данных"
    assert human_choice_label("Анализ 2–3 дня") == "Анализ 2–3 дня"
    template = Path("app/web/templates/web/settings.html").read_text()
    assert "ui-segments" in template and "segment('/app/settings?tab='" in template


def test_chat_and_notification_presentation_contracts():
    chat = Path("app/web/templates/web/chat.html").read_text()
    js = Path("app/static/js/chat.js").read_text()
    base = Path("app/web/templates/web/base.html").read_text()
    assert 'class="chat-composer"' in chat and 'class="chat-suggestions"' in chat
    assert 'message.role !== \'user\' && message.id' in chat  # one delete action per side
    assert 'requestConfirmation' in js and 'confirm(' not in js and 'alert(' not in js
    assert 'if (!text || this.pending) return' in js
    assert 'safeMarkdown(html)' in js and 'el.removeAttribute(attr.name)' in js
    assert '<template x-teleport="body">' in base and '@click.self="close()"' in base
    assert 'aria-modal="true"' in base and 'trapFocus($event)' in base
    assert 'document.body.style.overflow = "hidden"' in base
    for path in Path("app/templates").rglob("*.html"):
        assert 'alert(' not in path.read_text(), path


def test_chat_head_has_real_assets_and_css_contains_no_script_fragments():
    from html.parser import HTMLParser
    from jinja2 import DictLoader, Environment, ChoiceLoader

    env = Environment(loader=ChoiceLoader([
        DictLoader({"web/base.html": "<html><head>{% block extra_head %}{% endblock %}</head><body>{% block content %}{% endblock %}</body></html>"}),
        templates.env.loader,
    ]))
    html = env.get_template("web/chat.html").render(
        messages=[], csrf="fixture", is_owner=True,
        url_for=lambda name, **kwargs: "/static/" + kwargs["path"],
    )
    class AssetParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.assets = []
            self.scripts = []
            self.in_script = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag in ("link", "script"):
                self.assets.append((tag, attrs))
            if tag == "script":
                self.in_script = True

        def handle_endtag(self, tag):
            if tag == "script":
                self.in_script = False

        def handle_data(self, data):
            if self.in_script:
                self.scripts.append(data)

    parsed = AssetParser()
    parsed.feed(html)
    assert any(tag == "link" and attrs.get("href") == "/static/css/chat.css" for tag, attrs in parsed.assets)
    assert any(tag == "script" and attrs.get("src") == "/static/js/chat.js" for tag, attrs in parsed.assets)
    assert any("marked.setOptions" in text for text in parsed.scripts)
    assert all("<link" not in text and "<script" not in text for text in parsed.scripts)
    css = Path("app/static/css/chat.css").read_text()
    assert "<script" not in css and "</script>" not in css and "<style>" not in css
    assert "marked.setOptions" not in css
