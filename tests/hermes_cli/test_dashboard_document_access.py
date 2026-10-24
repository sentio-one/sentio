"""A granted non-admin can load the dashboard app itself (private fork, found on the first invited sign-in).

The REST gate is default-deny, but its default must apply to ``/api/*`` only: the single-page app's own document loads
(``/``, ``/access`` and every other client-side route) carry no data, and refusing them with ``admin_required`` left an
invited person staring at a JSON error instead of the app. The data behind the app is still gated route by route.
"""
import pytest

from hermes_cli.dashboard_auth.access_http import classify



@pytest.mark.parametrize("path", ["/", "/access", "/chat", "/sessions/abc", "/some/deep/client/route"])
def test_the_app_documents_load_for_a_granted_non_admin(path):
    assert classify("GET", path) == "open"
    assert classify("HEAD", path) == "open"


@pytest.mark.parametrize("method,path", [("POST", "/"), ("PUT", "/access"), ("DELETE", "/chat"), ("PATCH", "/x")])
def test_only_reading_a_document_is_open(method, path):
    assert classify(method, path) == "admin"


@pytest.mark.parametrize("path", ["/api", "/api/", "/apix", "/api/brand-new-route"])
def test_the_api_stays_default_deny(path):
    if path == "/apix":  # not under /api: a document path like any other
        assert classify("GET", path) == "open"
    else:
        assert classify("GET", path) == "admin"
