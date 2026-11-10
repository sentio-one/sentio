"""Operators review drafts from the phone with short typed commands, like the legacy gate's."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.business.config import BusinessConfig
from gateway.business.operator_notices import edit_text, rejection_text, review_card_text
from gateway.business.operator_shorthand import handle_business_operator_shorthand, parse_shorthand
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from gateway.config import GatewayConfig, Platform
from gateway.platforms.event import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource

REVISION = "4a1b2cde-0000-4000-8000-000000000001"
CODE = "4a1b2c"


@pytest.mark.parametrize("text,expected", [
    ("edit 4a1b2c Hi there, how can I help?", ("edit", CODE, "Hi there, how can I help?")),
    ("Edit 4A1B2C: line one\nline two", ("edit", CODE, "line one\nline two")),
    ("ok 4a1b2c", ("approve", CODE, "")),
    ("yes 4a1b2c", ("approve", CODE, "")),
    ("send 4a1b2c", ("approve", CODE, "")),
    ("no 4a1b2c too pushy", ("reject", CODE, "too pushy")),
    ("discard 4a1b2c", ("reject", CODE, "")),
    ("edit 4a1b2c", ("edit", CODE, "")),
])
def test_commands_are_recognised(text, expected):
    assert parse_shorthand(text) == expected


@pytest.mark.parametrize("text", ["hello there", "ok", "ok 12", "okay 4a1b2c", "ok 4a1b2cd", "no thanks 4a1b2c", ""])
def test_ordinary_chat_is_not_a_command(text):
    assert parse_shorthand(text) is None


def test_card_shows_what_the_lead_said_the_draft_and_the_options():
    text = review_card_text(code=CODE, label="+27829533285", said=["mhh this seems interesting", "send the link"],
                            draft="Of course, here is the link.")
    assert text.startswith(f"🕓 Draft {CODE} for +27829533285")
    assert 'They said: "mhh this seems interesting"' in text and 'They said: "send the link"' in text
    assert "Draft reply:\nOf course, here is the link." in text
    assert "👍" in text and "👎" in text and f"edit {CODE} your text" in text


def test_card_for_an_opener_has_no_they_said_line():
    text = review_card_text(code=CODE, label="+27829533285", said=[], draft="Good morning.")
    assert "They said" not in text and "Draft reply:\nGood morning." in text


def test_a_long_lead_message_is_trimmed():
    text = review_card_text(code=CODE, label="x", said=["a" * 900], draft="d")
    assert "a" * 300 in text and "a" * 301 not in text


def test_rejection_and_edit_notices_are_plain_language():
    assert rejection_text({"ok": True}, label="+2782") == "🗑 Discarded the draft for +2782. Nothing was sent."
    assert "Nothing was sent" in rejection_text({"ok": False, "error": "revision_not_found"}, label="+2782")
    ok = edit_text({"ok": True, "approval_count": 1}, label="+2782", text="My version")
    assert ok.startswith("✏️ Your version is approved for +2782") and ok.endswith("My version")
    failed = edit_text({"ok": True, "state": "attention", "revision": {"checks": {"issues": [{"code": "link", "reason": "contains a link"}]}}},
                       label="+2782", text="x")
    assert "did not pass" in failed and "contains a link" in failed and "Nothing was sent" in failed


def _setup(tmp_path, operator="123"):
    store = BusinessStore(tmp_path)
    service = BusinessService(store, config_resolver=BusinessConfig("supervised", (), (operator,), 2, 300, 600, None))
    service.runtime_profile = "default"
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig()
    runner._business_service = service
    runner._is_user_authorized_for_source = lambda source: True
    service.runner = runner
    with store._connect() as db:
        db.execute("INSERT INTO business_operator_cards(message_id,runtime_profile,transport_profile,account_id,contact_id,digest_id,revision_id,plan_digest,created_at_ms) VALUES(?,?,?,?,?,?,?,?,?)",
                   ("card-1", "default", "default", "default", "customer@s.whatsapp.net", "dig-1", REVISION, "sha256:exact", 1))
    return runner, store


def _event(text, user="123@s.whatsapp.net"):
    source = SessionSource(platform=Platform.WHATSAPP, chat_id=user, chat_type="dm", user_id=user)
    return MessageEvent(text=text, message_type=MessageType.TEXT, source=source)


@pytest.fixture
def seams(monkeypatch):
    notice = AsyncMock(return_value={"ok": True})
    approval = AsyncMock(return_value={"approval_count": 1})
    execute = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr("gateway.business.transport.send_operator_notice", notice)
    monkeypatch.setattr("gateway.business.operators.handle_operator_event", approval)
    monkeypatch.setattr("gateway.business.control.execute_business_request", execute)
    return SimpleNamespace(notice=notice, approval=approval, execute=execute)


@pytest.mark.asyncio
async def test_ok_approves_the_exact_card_revision(tmp_path, seams):
    runner, _ = _setup(tmp_path)
    assert await handle_business_operator_shorthand(runner, _event(f"ok {CODE}")) is True
    payload = seams.approval.await_args.args[1]
    assert (payload["action"], payload["revision_id"], payload["plan_digest"], payload["digest_id"]) == ("shorthand", REVISION, "sha256:exact", "dig-1")
    assert "Approved" in seams.notice.await_args.args[2]


@pytest.mark.asyncio
async def test_no_discards_with_the_operators_reason(tmp_path, seams):
    runner, _ = _setup(tmp_path)
    assert await handle_business_operator_shorthand(runner, _event(f"no {CODE} too pushy")) is True
    kwargs = seams.execute.await_args.kwargs
    assert kwargs["operation"] == "operator.reject"
    assert kwargs["params"]["revision_id"] == REVISION and kwargs["params"]["reason"] == "too pushy"
    assert "Discarded" in seams.notice.await_args.args[2]
    assert seams.approval.await_count == 0


@pytest.mark.asyncio
async def test_edit_replaces_the_draft_then_approves_the_new_version(tmp_path, seams, monkeypatch):
    runner, _ = _setup(tmp_path)
    new_plan = {"scope": {"runtime_profile": "default", "transport_profile": "default", "account_id": "default", "contact_id": "customer@s.whatsapp.net"},
                "revision_id": "new-revision", "digest": "sha256:new"}
    seams.execute.return_value = {"ok": True, "revision": {"id": "new-revision"}, "plan": new_plan}
    card = {"scope": SimpleNamespace(), "digest_id": "dig-2", "revision_id": "new-revision", "plan_digest": "sha256:new"}
    monkeypatch.setattr("gateway.business.operators._review_card", lambda *_a, **_k: card)
    assert await handle_business_operator_shorthand(runner, _event(f"edit {CODE} Hi, my own words")) is True
    kwargs = seams.execute.await_args.kwargs
    assert kwargs["operation"] == "operator.edit" and kwargs["params"]["text"] == "Hi, my own words"
    payload = seams.approval.await_args.args[1]
    assert (payload["revision_id"], payload["plan_digest"], payload["digest_id"]) == ("new-revision", "sha256:new", "dig-2")
    assert seams.notice.await_args.args[2].startswith("✏️ Your version is approved")


@pytest.mark.asyncio
async def test_an_edit_that_fails_the_checks_sends_nothing(tmp_path, seams):
    runner, _ = _setup(tmp_path)
    seams.execute.return_value = {"ok": True, "state": "attention", "reason": "checks_failed",
                                  "revision": {"checks": {"issues": [{"code": "exclamation", "reason": "contains an exclamation mark"}]}}}
    assert await handle_business_operator_shorthand(runner, _event(f"edit {CODE} Great!")) is True
    assert seams.approval.await_count == 0
    assert "exclamation mark" in seams.notice.await_args.args[2]


@pytest.mark.asyncio
async def test_edit_without_text_explains_the_format(tmp_path, seams):
    runner, _ = _setup(tmp_path)
    assert await handle_business_operator_shorthand(runner, _event(f"edit {CODE}")) is True
    assert seams.execute.await_count == 0 and f"edit {CODE} " in seams.notice.await_args.args[2]


@pytest.mark.asyncio
async def test_an_unknown_code_is_explained_and_changes_nothing(tmp_path, seams):
    runner, _ = _setup(tmp_path)
    assert await handle_business_operator_shorthand(runner, _event("ok ffffff")) is True
    assert seams.approval.await_count == 0 and seams.execute.await_count == 0
    assert "ffffff" in seams.notice.await_args.args[2]


@pytest.mark.asyncio
async def test_only_a_verified_operator_can_use_the_commands(tmp_path, seams):
    runner, _ = _setup(tmp_path)
    assert await handle_business_operator_shorthand(runner, _event(f"ok {CODE}", user="999@s.whatsapp.net")) is False
    assert seams.approval.await_count == 0 and seams.notice.await_count == 0


@pytest.mark.asyncio
async def test_ordinary_operator_chat_is_left_alone(tmp_path, seams):
    runner, _ = _setup(tmp_path)
    assert await handle_business_operator_shorthand(runner, _event("thanks, looks good")) is False
    assert seams.notice.await_count == 0


@pytest.mark.asyncio
async def test_thumbs_down_on_a_card_discards_it(tmp_path, seams):
    from gateway.business.operators import handle_business_operator_reaction
    runner, _ = _setup(tmp_path)
    event = _event("")
    event.metadata = {"reaction_target_id": "card-1", "reaction_emoji": "👎"}
    assert await handle_business_operator_reaction(runner, event) is True
    assert seams.execute.await_args.kwargs["operation"] == "operator.reject"
    assert seams.approval.await_count == 0 and "Discarded" in seams.notice.await_args.args[2]
