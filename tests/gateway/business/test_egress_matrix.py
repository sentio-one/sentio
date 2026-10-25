"""Every adapter egress method is held by native authority in supervised mode (Task B7)."""

from __future__ import annotations


import pytest

from gateway.business.config import BusinessConfig
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from gateway.config import PlatformConfig
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource
from gateway.config import Platform
from plugins.platforms.whatsapp.adapter import WhatsAppAdapter

CUSTOMER = "27600000005@s.whatsapp.net"


class _ForbiddenSession:
    """Any direct bridge call is an egress bypass."""

    def __init__(self):
        self.calls: list[str] = []

    def _record(self, *args, **kwargs):
        self.calls.append(str(args[:1]))
        raise AssertionError("physical bridge call bypassed native approval")

    post = get = put = delete = request = _record


def _adapter(tmp_path, mode="supervised"):
    store = BusinessStore(tmp_path)
    store.bind_contact("default", "default", "default", [CUSTOMER])
    service = BusinessService(store, config_resolver=lambda _: BusinessConfig(
        mode, (), ("operator",), 2, 86400, 604800, None))
    adapter = WhatsAppAdapter(PlatformConfig(enabled=True, extra={"session_path": str(tmp_path / "session")}))
    adapter._hermes_profile_name = "default"
    adapter._business_service = service
    adapter._http_session = _ForbiddenSession()
    adapter._running = True
    return adapter, store


@pytest.mark.asyncio
async def test_every_adapter_send_path_is_held_without_touching_the_bridge(tmp_path):
    adapter, store = _adapter(tmp_path)
    media = tmp_path / "photo.jpg"
    media.write_bytes(b"\xff\xd8\xff\xd9")
    results = [
        await adapter.send(CUSTOMER, "hello"),
        await adapter.edit_message(CUSTOMER, "wamid-1", "edited"),
        await adapter.send_poll(CUSTOMER, "Pick one", ["a", "b"]),
        await adapter.send_location(CUSTOMER, 1.5, 2.5, name="HQ"),
        await adapter._send_media_to_bridge(CUSTOMER, str(media), "image", "caption"),
    ]
    assert [r.success for r in results] == [False] * 5
    assert all(r.business_receipt for r in results)
    assert adapter._http_session.calls == []
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) AS n FROM business_attempts").fetchone()["n"] == 0


@pytest.mark.asyncio
async def test_failed_turn_creates_no_outbox_entry(business_gateway):
    source = SessionSource(platform=Platform.WHATSAPP, chat_id=CUSTOMER, user_id=CUSTOMER, profile="default")
    event = MessageEvent(text="Place order", source=source, message_id="inbound-order")
    await business_gateway.receive(event)
    receipt = await business_gateway.finish(
        {"final_response": "Traceback: internal DB error", "failed": True, "api_calls": 2}, event=event)
    assert receipt.state in ("failed", "attention")
    with business_gateway.store._connect() as db:
        assert db.execute("SELECT COUNT(*) AS n FROM business_outbox").fetchone()["n"] == 0
    assert business_gateway.socket_calls == []
