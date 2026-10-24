"""Tests for purpose-bound vault payload sealing and unsealing (Task C3)."""

from __future__ import annotations

import pytest

from agent.vault_store import VaultError, VaultStore


@pytest.fixture
def vault(tmp_path):
    return VaultStore(base_dir=tmp_path / "vault")


def test_seal_and_open_message_payload(vault):
    binding = {
        "runtime_profile": "default",
        "transport_profile": "whatsapp",
        "account_id": "acc-1",
        "contact_id": "27600000001@s.whatsapp.net",
        "secret_version": "v1",
    }
    payload = {
        "text": "Your temporary demo code is SECRET-12345",
        "credential_item_id": "vitem-demo-1",
    }

    handle = vault.seal_message_payload(binding, payload)
    assert isinstance(handle, str)
    assert handle.startswith("vpay-")

    opened = vault.open_message_payload(handle)
    assert opened["payload"]["text"] == "Your temporary demo code is SECRET-12345"
    assert opened["binding"]["contact_id"] == "27600000001@s.whatsapp.net"


def test_open_missing_or_tampered_payload_fails(vault):
    with pytest.raises(VaultError):
        vault.open_message_payload("vpay-nonexistent")

    binding = {"contact_id": "27600000001@s.whatsapp.net"}
    payload = {"secret": "abc"}
    handle = vault.seal_message_payload(binding, payload)

    # Tamper payload file
    payload_file = vault._base / "payloads" / f"{handle}.enc"
    assert payload_file.exists()
    payload_file.write_bytes(b"corrupted_bytes")

    with pytest.raises(VaultError):
        vault.open_message_payload(handle)
