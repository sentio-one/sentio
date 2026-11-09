"""The stock bridge's reaction payload becomes the metadata the operator-approval handler reads."""
import pytest

from gateway.config import PlatformConfig
from plugins.platforms.whatsapp.adapter import WhatsAppAdapter

# The shape scripts/whatsapp-bridge/bridge_helpers.js emits for a reactionMessage.
BRIDGE_REACTION = {
    "messageId": "REACT1", "chatId": "27648873157@s.whatsapp.net", "senderId": "27648873157@s.whatsapp.net",
    "senderName": "Op", "body": "[Reaction: 👍 to CARD1]", "mediaType": "reaction", "nativeType": "reactionMessage",
    "nativeMetadata": {"reaction": {"text": "👍", "messageId": "CARD1", "remoteJid": "27648873157@s.whatsapp.net", "participant": ""}},
}


@pytest.mark.asyncio
async def test_bridge_reaction_exposes_target_and_emoji(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "27648873157")
    adapter = WhatsAppAdapter(PlatformConfig(enabled=True, extra={"session_path": str(tmp_path / "wa")}))

    event = await adapter._build_message_event(dict(BRIDGE_REACTION))

    assert event.metadata["reaction_target_id"] == "CARD1"
    assert event.metadata["reaction_emoji"] == "👍"
