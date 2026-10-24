// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { bitrateMenuDecision } = require('./browser-audio-delay-smoke');
test('bitrate probe waits for idle owners and verifies viewer controls from authority, not disabled state', () => {
  assert.equal(bitrateMenuDecision(undefined, true), 'wait');
  assert.equal(bitrateMenuDecision({ canControl: true, transactionStatus: 'resizing' }, true), 'wait');
  assert.equal(bitrateMenuDecision({ canControl: true, transactionStatus: 'idle' }, true), 'wait');
  assert.equal(bitrateMenuDecision({ canControl: true, transactionStatus: 'idle' }, false), 'owner');
  assert.equal(bitrateMenuDecision({ canControl: false, transactionStatus: 'idle' }, true), 'viewer');
  assert.equal(bitrateMenuDecision({ canControl: false, transactionStatus: 'idle' }, false), 'error');
});
