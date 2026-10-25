export async function dispatchAuthorized(request, options = {}) {
  const { authorize, journal, sendSocket } = options;

  if (!(request.authorization_token || request.token)) {
    return { ok: false, error: 'business_authorization_required' };
  }

  if (typeof authorize !== "function" || typeof sendSocket !== "function") return {ok:false,error:"business_authority_unavailable"};

  const attemptId = request.attempt_id || request.authorization_token;
  if (journal?.recordAttempt) {
    journal.recordAttempt({
      attempt_id: attemptId,
      chat_id: request.chatId,
      payload_digest: request.payload_digest,
    });
  }

  if (authorize) {
    const authRes = await authorize({
      ...request,
      token: request.authorization_token || request.token,
      payload_digest: request.payload_digest,
    });
    if (!authRes || !authRes.ok) {
      const err = authRes?.error || 'authorization_failed';
      if (journal?.recordOutcome) {
        journal.recordOutcome({
          attempt_id: attemptId,
          success: false,
          error: err,
        });
      }
      return { ok: false, error: err };
    }
  }

  try {
    let sent = null;
    if (sendSocket) {
      sent = await sendSocket(request);
    }

    const messageId = sent?.key?.id || sent?.id || null;
    if (journal?.recordOutcome) {
      journal.recordOutcome({
        attempt_id: attemptId,
        success: true,
        message_id: messageId,
      });
    }

    return {
      ok: true,
      messageId,
      sent,
    };
  } catch (err) {
    if (journal?.recordOutcome) {
      journal.recordOutcome({
        attempt_id: attemptId,
        success: false,
        error: err.message,
      });
    }
    return { ok: false, error: err.message, submitted: true, state: 'unknown' };
  }
}
