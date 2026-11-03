"""Strict-template contracts with synthetic catalog states; no DB or network."""

import pytest
from bs4 import BeautifulSoup

from tests.shared.file_catalog_layout import render

__source_only__ = True


@pytest.mark.parametrize("case", ["list", "no-mounts", "empty", "search", "detail", "long-name"])
def test_catalog_states_render_without_provider_actions(case):
    html = BeautifulSoup(render(case), "html.parser")
    assert html.select_one('[href$="css/file_catalog.css"]')
    assert html.select_one("[data-file-catalog], [data-file-catalog-detail]")
    assert not html.select('#page-content form[method="post"], #page-content a[href^="http"]')
    assert "Только метаданные" in html.get_text()
    if case == "detail":
        assert "не версии содержимого" in html.get_text()
        assert "Выводы для этого файла в каталоге не сохранены" in html.get_text()
        assert len(html.select(".file-catalog-history li")) == 1
    elif case == "no-mounts":
        assert not html.select(".file-catalog-search")
        assert "Нет доступных подключённых папок" in html.get_text()
    else:
        assert html.select_one('label[for="file-mount"]')
        assert html.select_one('label[for="file-query"]')
        assert html.select_one('#file-query[maxlength="200"]')
        assert html.select_one('form[method="get"]')


def test_catalog_empty_is_not_a_synced_empty_folder():
    html = BeautifulSoup(render("empty"), "html.parser")
    assert "Это не означает, что папка пуста" in html.get_text()
    assert html.select_one("[data-file-catalog-empty]")
    assert not html.select(".file-catalog-name")


def test_catalog_links_carry_only_local_identity():
    html = BeautifulSoup(render("list"), "html.parser")
    assert [a["href"] for a in html.select(".file-catalog-name")] == [
        f"/app/knowledge/files/{i}?connection_id=17" for i in range(101, 104)
    ]
    assert html.select_one("#file-mount option")["value"] == "17"
