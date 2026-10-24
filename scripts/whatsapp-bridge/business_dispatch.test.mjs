import test from 'node:test';
import assert from 'node:assert/strict';
import { dispatchAuthorized } from './business_dispatch.js';

test('dispatchAuthorized requires authorization token', async () => {
  const request = { chatId: '123@s.whatsapp.net', message: 'hi' };
  const res = await dispatchAuthorized(request, {});
  assert.equal(res.ok, false);
  assert.equal(res.error, 'business_authorization_required');
});

test('dispatchAuthorized handles rejection from authorize callback', async () => {
  const request = {
    chatId: '123@s.whatsapp.net',
    message: 'hi',
    authorization_token: 'tok-1',
    payload_digest: 'sha256:abc',
  };
  const fakeAuthorize = async () => ({ ok: false, error: 'authorization_consumed' });
  const socketCalls = [];
  const fakeSendSocket = async (payload) => {
    socketCalls.push(payload);
  };

  const res = await dispatchAuthorized(request, {
    authorize: fakeAuthorize,
    sendSocket: fakeSendSocket,
  });
  assert.equal(res.ok, false);
  assert.equal(res.error, 'authorization_consumed');
  assert.equal(socketCalls.length, 0);
});

test('dispatchAuthorized journals attempt and calls socket when authorized', async () => {
  const request = {
    chatId: '123@s.whatsapp.net',
    message: 'hi',
    authorization_token: 'tok-valid',
    payload_digest: 'sha256:abc',
  };
  const journalEntries = [];
  const fakeJournal = {
    recordAttempt: (entry) => {
      journalEntries.push(entry);
    },
    recordOutcome: (entry) => {
      journalEntries.push(entry);
    },
  };
  const fakeAuthorize = async () => ({ ok: true, attemptId: 'att-1' });
  const socketCalls = [];
  const fakeSendSocket = async (payload) => {
    socketCalls.push(payload);
    return { key: { id: 'msg-out-1' } };
  };

  const res = await dispatchAuthorized(request, {
    authorize: fakeAuthorize,
    journal: fakeJournal,
    sendSocket: fakeSendSocket,
  });
  assert.equal(res.ok, true);
  assert.equal(res.messageId, 'msg-out-1');
  assert.equal(socketCalls.length, 1);
  assert.equal(journalEntries.length, 2);
});
