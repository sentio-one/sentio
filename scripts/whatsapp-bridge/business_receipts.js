/**
 * Normalization of Baileys message receipt updates into typed business receipts (Task B6).
 */

const STATUS_NAMES = {
  0: "ERROR",
  1: "PENDING",
  2: "SERVER_ACK",
  3: "DELIVERY_ACK",
  4: "READ",
  5: "PLAYED",
};

const STATUS_STATES = {
  0: "failed",
  1: "pending",
  2: "accepted",
  3: "delivered",
  4: "read",
  5: "played",
};

/**
 * Normalize an array of Baileys messages.update events into typed business receipt objects.
 *
 * @param {Array<object>} updates - Raw updates from Baileys messages.update
 * @param {object} options - Options containing account and optional statusEnum
 * @returns {Array<object>} Normalized delivery receipts
 */
export function normalizeDeliveryUpdates(updates, { account, statusEnum } = {}) {
  if (!Array.isArray(updates)) {
    return [];
  }

  const normalized = [];
  for (const item of updates) {
    if (!item || !item.key) {
      continue;
    }
    // Only outbound messages have delivery receipts
    if (item.key.fromMe !== true) {
      continue;
    }

    const messageId = item.key.id;
    if (!messageId) {
      continue;
    }

    const update = item.update || {};
    const status = update.status;
    if (typeof status !== "number") {
      continue;
    }

    let statusName = STATUS_NAMES[status];
    if (statusEnum) {
      for (const [name, val] of Object.entries(statusEnum)) {
        if (val === status) {
          statusName = name;
          break;
        }
      }
    }

    const state = STATUS_STATES[status] || "unknown";
    normalized.push({
      account: account || null,
      messageId,
      remoteJid: item.key.remoteJid || null,
      status,
      statusName: statusName || String(status),
      state,
      timestampMs: Date.now(),
    });
  }

  return normalized;
}
