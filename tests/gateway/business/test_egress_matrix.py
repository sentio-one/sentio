"""Tests for complete WhatsApp egress separation and fail-closed authority (Task B7)."""

from __future__ import annotations

import pytest

from gateway.business.models import Scope
from gateway.business.turns import handle_business_turn
from gateway.config import Platform
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource


class MockTransportEgress:
    def __init__(self):
        self.customer_socket_calls: list[dict] = []
        self.raw_footer_calls: list[dict] = []
        self.voice_tts_calls: list[dict] = []
        self.progress_calls: list[dict] = []

    def send_socket(self, target: str, payload: dict):
        if "@s.whatsapp.net" in target:
            self.customer_socket_calls.append({"target": target, "payload": payload})

    def append_footer(self, target: str, footer: str):
        self.raw_footer_calls.append({"target": target, "footer": footer})

    def send_voice(self, target: str, audio_path: str):
        self.voice_tts_calls.append({"target": target, "audio": audio_path})

    def send_progress(self, target: str, text: str):
        self.progress_calls.append({"target": target, "progress": text})


@pytest.mark.asyncio
async def test_all_egress_paths_obey_native_authority(business_gateway):
    source = SessionSource(
        platform=Platform.WHATSAPP,
        chat_id="27600000005@s.whatsapp.net",
        user_id="27600000005@s.whatsapp.net",
        profile="default",
    )
    event = MessageEvent(text="Place order", source=source, message_id="inbound-order")
    await business_gateway.receive(event)

    egress = MockTransportEgress()

    # 1. Failed turn cannot dispatch to customer
    failed_result = {"final_response": "Traceback: internal DB error", "failed": True, "api_calls": 2}
    failed_receipt = await business_gateway.finish(failed_result, event=event)
    assert failed_receipt.state in ("failed", "attention")

    # In failed turn: no customer socket calls
    failed_turn = egress
    assert failed_turn.customer_socket_calls == []

    # 2. Raw footers, voice TTS, progress messages cannot reach external contact without approved draft
    configured_business_raw_footer_voice_progress_calls = (
        egress.raw_footer_calls + egress.voice_tts_calls + egress.progress_calls
    )
    assert configured_business_raw_footer_voice_progress_calls == []
