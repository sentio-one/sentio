"""A decision may follow working prose; the prose is never parsed as the decision."""

import pytest

from gateway.business.compose import _extract_json_payload
from gateway.business.models import BusinessProtocolError

DECISION = '{"kind":"compose","reason":"greet","intent":"greeting","fact_ids":[],"action_ids":[],"policy_version":"v1"}'


def test_trailing_unfenced_object_after_prose_is_extracted():
    assert _extract_json_payload(f"Hi there!\n\n{DECISION}")["intent"] == "greeting"


def test_braces_in_prose_do_not_hide_the_trailing_object():
    assert _extract_json_payload(f"Use {{name}} here.\n{DECISION}")["kind"] == "compose"


def test_object_followed_by_more_prose_is_rejected():
    with pytest.raises(BusinessProtocolError):
        _extract_json_payload(f"{DECISION}\nand then I kept talking")


def test_prose_only_is_rejected():
    with pytest.raises(BusinessProtocolError):
        _extract_json_payload("Hi there!")
