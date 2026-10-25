import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  renderBusinessPlan,
  digestDeliveryParts,
} from './business_render.js';

test('digestDeliveryParts produces stable sha256 digest covering all binding fields', () => {
  const parts = [
    { index: 0, operation: 'text', payload: { text: 'Hello customer!' }, media_hash: null },
  ];
  const binding = {
    scope: {
      runtime_profile: 'default',
      transport_profile: 'whatsapp',
      account_id: 'account-1',
      contact_id: '27600000001@s.whatsapp.net',
    },
    revision_id: 'rev-1',
    context_version: 1,
    policy_version: 'v1',
    lesson_version: 0,
    expires_at_ms: 1000000,
    reply_target: null,
    mentions: [],
  };

  const digest1 = digestDeliveryParts(parts, { binding });
  assert.ok(digest1.startsWith('sha256:'));

  const digest2 = digestDeliveryParts(parts, { binding });
  assert.equal(digest1, digest2);

  // 1. Changing reply_target changes digest
  const replyDigest = digestDeliveryParts(parts, { binding: { ...binding, reply_target: 'msg-parent-1' } });
  assert.notEqual(replyDigest, digest1);

  // 2. Changing mentions changes digest
  const mentionsDigest = digestDeliveryParts(parts, { binding: { ...binding, mentions: ['27600000002@s.whatsapp.net'] } });
  assert.notEqual(mentionsDigest, digest1);

  // 3. Changing media_hash changes digest
  const partsWithMedia1 = [
    { index: 0, operation: 'media', payload: { path: '/tmp/doc.pdf' }, media_hash: 'sha256:hash1' },
  ];
  const partsWithMedia2 = [
    { index: 0, operation: 'media', payload: { path: '/tmp/doc.pdf' }, media_hash: 'sha256:hash2' },
  ];
  const mediaDigest1 = digestDeliveryParts(partsWithMedia1, { binding });
  const mediaDigest2 = digestDeliveryParts(partsWithMedia2, { binding });
  assert.notEqual(mediaDigest1, mediaDigest2);
});

test('renderBusinessPlan renders text, chunks long messages, and sets digest', async () => {
  const request = {
    scope: {
      runtime_profile: 'default',
      transport_profile: 'whatsapp',
      account_id: 'account-1',
      contact_id: '27600000001@s.whatsapp.net',
    },
    revision_id: 'rev-1',
    context_version: 1,
    policy_version: 'v1',
    lesson_version: 0,
    candidate: {
      kind: 'text',
      text: 'Simple text reply',
      data: {},
      credential_slots: [],
    },
    now_ms: 1000000,
  };

  const plan = await renderBusinessPlan(request);
  assert.equal(plan.revision_id, 'rev-1');
  assert.equal(plan.parts.length, 1);
  assert.equal(plan.parts[0].operation, 'text');
  assert.equal(plan.parts[0].payload.text, 'Simple text reply');
  assert.ok(plan.digest.startsWith('sha256:'));
});

test('renderBusinessPlan checks quote target in messageStore and fails if missing or expired', async () => {
  const fakeStore = {
    messages: new Map(),
    get(id) {
      return this.messages.get(id);
    },
  };

  const request = {
    scope: {
      runtime_profile: 'default',
      transport_profile: 'whatsapp',
      account_id: 'account-1',
      contact_id: '27600000001@s.whatsapp.net',
    },
    revision_id: 'rev-quote',
    context_version: 1,
    policy_version: 'v1',
    lesson_version: 0,
    candidate: {
      kind: 'text',
      text: 'Quoting a message',
      data: { reply_target: 'msg-target-1' },
      credential_slots: [],
    },
    now_ms: 1000000,
  };

  // 1. Missing target in store -> must throw quote_target_unavailable
  await assert.rejects(
    async () => renderBusinessPlan(request, { messageStore: fakeStore }),
    /quote_target_unavailable/,
  );

  // 2. Found in store -> succeeds
  fakeStore.messages.set('msg-target-1', {
    key: { id: 'msg-target-1', remoteJid: '27600000001@s.whatsapp.net' },
    message: { conversation: 'Original message' },
  });

  const plan = await renderBusinessPlan(request, { messageStore: fakeStore });
  assert.equal(plan.parts[0].payload.reply_target, 'msg-target-1');
});

test('renderBusinessPlan validates and captures attachment, rejecting symlinks and missing files', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'biz-render-test-'));
  try {
    const docPath = path.join(tmpDir, 'test.pdf');
    fs.writeFileSync(docPath, Buffer.from('%PDF-1.4 test document content'));

    const symlinkPath = path.join(tmpDir, 'symlinked.pdf');
    fs.symlinkSync(docPath, symlinkPath);

    const baseRequest = {
      scope: {
        runtime_profile: 'default',
        transport_profile: 'whatsapp',
        account_id: 'account-1',
        contact_id: '27600000001@s.whatsapp.net',
      },
      revision_id: 'rev-media',
      context_version: 1,
      policy_version: 'v1',
      lesson_version: 0,
      now_ms: 1000000,
    };

    // 1. Regular file -> succeeds, media_hash is populated
    const plan = await renderBusinessPlan({
      ...baseRequest,
      candidate: {
        kind: 'media',
        text: 'Document caption',
        data: { attachments: [{ path: docPath, type: 'document' }] },
        credential_slots: [],
      },
    }, { captureDir: path.join(tmpDir, 'captured') });

    const mediaPart = plan.parts.find((p) => p.operation === 'media');
    assert.ok(mediaPart);
    assert.ok(mediaPart.media_hash.startsWith('sha256:'));

    // 2. Symlink -> throws symlinked_attachment
    await assert.rejects(
      async () => renderBusinessPlan({
        ...baseRequest,
        candidate: {
          kind: 'media',
          text: 'Symlink caption',
          data: { attachments: [{ path: symlinkPath, type: 'document' }] },
          credential_slots: [],
        },
      }),
      /symlinked_attachment/,
    );

    // 3. Missing file -> throws missing_attachment
    await assert.rejects(
      async () => renderBusinessPlan({
        ...baseRequest,
        candidate: {
          kind: 'media',
          text: 'Missing caption',
          data: { attachments: [{ path: path.join(tmpDir, 'nonexistent.pdf'), type: 'document' }] },
          credential_slots: [],
        },
      }),
      /missing_attachment/,
    );
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('renderBusinessPlan rejects credential slots until C3', async () => {
  const request = {
    scope: {
      runtime_profile: 'default',
      transport_profile: 'whatsapp',
      account_id: 'account-1',
      contact_id: '27600000001@s.whatsapp.net',
    },
    revision_id: 'rev-creds',
    context_version: 1,
    policy_version: 'v1',
    lesson_version: 0,
    candidate: {
      kind: 'text',
      text: 'My secret',
      data: {},
      credential_slots: ['api_token'],
    },
    now_ms: 1000000,
  };

  await assert.rejects(
    async () => renderBusinessPlan(request),
    /unresolved_credentials/,
  );
});

test('cross runtime digest parity with Python', () => {
  const parts = [
    { index: 0, operation: 'text', payload: { text: 'Hello customer!' }, media_hash: null },
  ];
  const binding = {
    scope: { runtime_profile: 'default', transport_profile: 'whatsapp', account_id: 'account-1', contact_id: '27600000001@s.whatsapp.net' },
    revision_id: 'rev-1',
    context_version: 1,
    policy_version: 'v1',
    lesson_version: 0,
    expires_at_ms: 1000000,
    reply_target: null,
    mentions: [],
  };
  const digest = digestDeliveryParts(parts, { binding });
  assert.equal(digest, 'sha256:dbeadd4405d17aa9d7eb8729b7902e7556a6603fd0236cdd055babbc53f8bb14');
});

