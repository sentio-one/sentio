import { createHash, createHmac, randomUUID } from 'crypto';
import fs from 'fs';
import path from 'path';
import { splitLongMessage } from './bridge_helpers.js';

export const ALLOWED_OPERATIONS = new Set(['text', 'media', 'edit', 'reaction', 'poll', 'location']);
export const DEFAULT_PLAN_TTL_MS = 300000;

function canonicalStringify(obj) {
  if (obj === null || typeof obj !== 'object') {
    if (typeof obj === 'number' && (!Number.isFinite(obj) || Number.isNaN(obj))) {
      throw new Error('NaN and Infinity are not permitted in canonical JSON');
    }
    return JSON.stringify(obj);
  }
  if (Array.isArray(obj)) {
    return '[' + obj.map(canonicalStringify).join(',') + ']';
  }
  const keys = Object.keys(obj).sort();
  return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonicalStringify(obj[k])).join(',') + '}';
}

export function digestDeliveryParts(parts, { binding = {}, hmacKey = null } = {}) {
  const scope = binding.scope || {};
  const canonicalBinding = {
    scope: {
      runtime_profile: String(scope.runtime_profile || ''),
      transport_profile: String(scope.transport_profile || ''),
      account_id: String(scope.account_id || ''),
      contact_id: String(scope.contact_id || ''),
    },
    revision_id: String(binding.revision_id || ''),
    context_version: Number(binding.context_version || 0),
    policy_version: String(binding.policy_version || ''),
    lesson_version: Number(binding.lesson_version || 0),
    expires_at_ms: Number(binding.expires_at_ms || 0),
    reply_target: binding.reply_target ?? null,
    mentions: Array.isArray(binding.mentions) ? [...binding.mentions].sort() : [],
  };

  const canonicalParts = (parts || []).map((p) => ({
    index: Number(p.index),
    operation: String(p.operation),
    payload: p.payload || {},
    media_hash: p.media_hash ?? null,
  }));

  const canonicalPayload = {
    binding: canonicalBinding,
    parts: canonicalParts,
  };

  const jsonString = canonicalStringify(canonicalPayload);
  if (hmacKey) {
    const hex = createHmac('sha256', hmacKey).update(jsonString, 'utf8').digest('hex');
    return `hmac-sha256:${hex}`;
  }

  const hex = createHash('sha256').update(jsonString, 'utf8').digest('hex');
  return `sha256:${hex}`;
}

function captureAttachment(filePath, { captureDir, accountId = '' } = {}) {
  if (!filePath || typeof filePath !== 'string') {
    throw new Error('invalid_attachment');
  }

  // Reject symlinks
  try {
    const stat = fs.lstatSync(filePath);
    if (stat.isSymbolicLink()) {
      throw new Error('symlinked_attachment: symlinks are prohibited');
    }
  } catch (err) {
    if (err.message.includes('symlinked_attachment')) throw err;
    throw new Error(`missing_attachment: ${filePath}`);
  }

  if (!fs.existsSync(filePath)) {
    throw new Error(`missing_attachment: ${filePath}`);
  }

  const buffer = fs.readFileSync(filePath);
  const hashHex = createHash('sha256').update(buffer).digest('hex');
  const mediaHash = `sha256:${hashHex}`;
  let finalPath = filePath;

  if (captureDir) {
    const destDir = path.join(captureDir, accountId);
    fs.mkdirSync(destDir, { recursive: true });
    const destFile = path.join(destDir, hashHex);
    if (!fs.existsSync(destFile)) {
      fs.writeFileSync(destFile, buffer);
    }
    finalPath = destFile;
  }

  return { finalPath, mediaHash };
}

export async function renderBusinessPlan(request, options = {}) {
  const { messageStore, captureDir } = options;
  const candidate = request.candidate || {};

  if (Array.isArray(candidate.credential_slots) && candidate.credential_slots.length > 0) {
    throw new Error('unresolved_credentials: credential slots rejected until C3');
  }

  const operation = candidate.kind;
  if (!ALLOWED_OPERATIONS.has(operation)) {
    throw new Error(`unsupported_operation: ${operation}`);
  }

  const data = candidate.data || {};
  const replyTarget = data.reply_target || data.reply_to || request.reply_target || null;
  if (replyTarget) {
    const quoted = messageStore?.get(replyTarget);
    if (!quoted) {
      throw new Error(`quote_target_unavailable: reply target ${replyTarget} not found in messageStore`);
    }
  }

  const mentions = Array.isArray(data.mentions) ? data.mentions : (request.mentions || []);
  const parts = [];

  if (operation === 'text') {
    const chunks = splitLongMessage(candidate.text || '');
    if (!chunks.length) chunks.push('');
    for (let i = 0; i < chunks.length; i += 1) {
      const payload = { text: chunks[i] };
      if (i === 0) {
        if (replyTarget) payload.reply_target = replyTarget;
        if (mentions.length) payload.mentions = [...mentions];
      }
      parts.push({
        index: i,
        operation: 'text',
        payload,
        media_hash: null,
      });
    }
  } else if (operation === 'media') {
    let attachments = data.attachments;
    if (!attachments && data.path) {
      attachments = [{ path: data.path, type: data.type || 'document' }];
    }
    if (!Array.isArray(attachments) || !attachments.length) {
      throw new Error('missing_attachment: no attachments specified');
    }

    for (let i = 0; i < attachments.length; i += 1) {
      const att = attachments[i];
      const { finalPath, mediaHash } = captureAttachment(att.path, {
        captureDir,
        accountId: request.scope?.account_id || '',
      });
      const payload = {
        path: finalPath,
        type: att.type || 'document',
        caption: i === 0 ? (candidate.text || '') : '',
      };
      if (i === 0) {
        if (replyTarget) payload.reply_target = replyTarget;
        if (mentions.length) payload.mentions = [...mentions];
      }
      parts.push({
        index: i,
        operation: 'media',
        payload,
        media_hash: mediaHash,
      });
    }
  } else {
    // other operations: edit, reaction, poll, location
    const payload = { ...data };
    if (candidate.text) payload.text = candidate.text;
    if (replyTarget) payload.reply_target = replyTarget;
    if (mentions.length) payload.mentions = [...mentions];
    parts.push({
      index: 0,
      operation,
      payload,
      media_hash: null,
    });
  }

  const nowMs = request.now_ms || Date.now();
  const expiresAtMs = request.expires_at_ms || (nowMs + DEFAULT_PLAN_TTL_MS);

  const binding = {
    scope: request.scope || {},
    revision_id: request.revision_id,
    context_version: request.context_version ?? 1,
    policy_version: request.policy_version ?? 'v1',
    lesson_version: request.lesson_version ?? 0,
    expires_at_ms: expiresAtMs,
    reply_target: replyTarget,
    mentions,
  };

  const digest = digestDeliveryParts(parts, { binding });
  const id = request.id || randomUUID();

  return {
    id,
    revision_id: request.revision_id,
    scope: request.scope,
    context_version: binding.context_version,
    policy_version: binding.policy_version,
    parts,
    expires_at_ms: expiresAtMs,
    digest,
  };
}
