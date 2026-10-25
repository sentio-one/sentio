import fs from 'fs';
import path from 'path';

export function createAttemptJournal(journalPath) {
  if (!journalPath) {
    return {
      recordAttempt() {},
      recordOutcome() {},
    };
  }

  const dir = path.dirname(journalPath);
  fs.mkdirSync(dir, { recursive: true });

  function appendFsync(data) {
    const line = JSON.stringify(data) + '\n';
    const fd = fs.openSync(journalPath, 'a');
    try {
      fs.writeSync(fd, line);
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
  }

  return {
    recordAttempt(entry) {
      appendFsync({ event: 'attempt', ...entry, timestamp: Date.now() });
    },
    recordOutcome(entry) {
      appendFsync({ event: 'outcome', ...entry, timestamp: Date.now() });
    },
  };
}
