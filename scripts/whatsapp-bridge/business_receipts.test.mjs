import test from "node:test";
import assert from "node:assert/strict";
import { normalizeDeliveryUpdates } from "./business_receipts.js";

const statusEnum = {
  ERROR: 0,
  PENDING: 1,
  SERVER_ACK: 2,
  DELIVERY_ACK: 3,
  READ: 4,
  PLAYED: 5,
};

test("normalizeDeliveryUpdates maps baileys message updates into typed receipts", () => {
  const updates = [
    {
      key: { id: "msg-1", remoteJid: "27600000001@s.whatsapp.net", fromMe: true },
      update: { status: 2 },
    },
    {
      key: { id: "msg-2", remoteJid: "27600000002@s.whatsapp.net", fromMe: true },
      update: { status: 3 },
    },
    {
      // Inbound or missing key - should be skipped
      key: { id: "msg-inbound", fromMe: false },
      update: { status: 2 },
    },
    {
      // No status - e.g. poll update without receipt status
      key: { id: "msg-poll", fromMe: true },
      update: { pollUpdates: [] },
    },
  ];

  const normalized = normalizeDeliveryUpdates(updates, {
    account: { transport_profile: "whatsapp", account_id: "account-1" },
    statusEnum,
  });

  assert.equal(normalized.length, 2);
  assert.equal(normalized[0].messageId, "msg-1");
  assert.equal(normalized[0].status, 2);
  assert.equal(normalized[0].statusName, "SERVER_ACK");
  assert.equal(normalized[0].state, "accepted");

  assert.equal(normalized[1].messageId, "msg-2");
  assert.equal(normalized[1].status, 3);
  assert.equal(normalized[1].statusName, "DELIVERY_ACK");
  assert.equal(normalized[1].state, "delivered");
});
