"""The sign-in pages the gateway serves must read as Sentio One.

They are the first screen a person sees when a company gateway asks them to sign in, and used to carry
Nous Research's brand mark and "Hermes Agent" in the title and subtitle.
"""
from __future__ import annotations

import re

import pytest

from hermes_cli.dashboard_auth import clear_providers, register_provider
from hermes_cli.dashboard_auth.login_page import render_login_html
from tests.hermes_cli.conftest_dashboard_auth import StubAuthProvider


@pytest.fixture(autouse=True)
def _providers():
    clear_providers()
    yield
    clear_providers()


def _visible_text(page: str) -> str:
    """The page with style/script blocks and tags removed: what a person reads, plus the <title>."""
    page = re.sub(r"<(style|script)\b.*?</\1>", "", page, flags=re.DOTALL | re.IGNORECASE)
    return re.sub(r"<[^>]+>", " ", page)


def _title(page: str) -> str:
    return re.search(r"<title>(.*?)</title>", page, re.DOTALL).group(1)


def test_the_sign_in_page_is_branded_sentio_one():
    register_provider(StubAuthProvider())
    page = render_login_html()
    text = _visible_text(page)

    assert "Sentio One" in _title(page)
    assert "Sentio" in text and "One" in text
    assert "Nous" not in text and "Research" not in text
    assert "Hermes" not in text and "Hermes" not in _title(page)


def test_the_unavailable_page_is_branded_sentio_one_and_does_not_link_to_nous_docs():
    page = render_login_html()  # no provider registered
    text = _visible_text(page)

    assert "Sign-in unavailable" in text
    assert "Sentio One" in _title(page)
    assert "Hermes" not in text and "Nous" not in text
    assert "nousresearch.com" not in page
