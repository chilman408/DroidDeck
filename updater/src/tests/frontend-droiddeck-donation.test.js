// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const root = process.env.TA_FRONTEND_DIR;
const url = 'https://www.paypal.com/donate/?business=TFWNLUC7ZGSPS&no_recurring=0&item_name=Thank+you+for+your+support%21++I+appreciate+it.&currency_code=USD';
test('Donation uses the exact Android 15 RPi4 message, recipient and linked QR', { skip: !root }, () => {
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const donation = html.match(/<section id="page-donations"[\s\S]*?<\/section>/)[0];
  assert.ok(donation.includes('This is a self funded project, your contribution means the world to me!'));
  assert.ok(donation.includes('href="' + url.replace(/&/g, '&amp;') + '"'));
  assert.match(donation, /target="_blank" rel="noopener noreferrer"/);
  assert.match(donation, /src="\.\/assets\/donations-qr\.svg"/);
  assert.doesNotMatch(donation, /community-founded|ongoing development of Tesla Android/);
});
test('Donation SVG is the exact patched RPi4 asset, not merely matching descriptive text', { skip: !root }, () => {
  const svg = fs.readFileSync(path.join(root, 'assets/donations-qr.svg'), 'utf8').replace(/\r\n/g, '\n');
  assert.equal(crypto.createHash('md5').update(svg).digest('hex'), '3450073a38e6d616d6ce6cdcaf9d0c22');
  assert.equal(crypto.createHash('sha256').update(svg).digest('hex'), '1935564279df3fa85e4fa93f4288d19a05d1b3c27f61ae6572039773f7deeee0');
  assert.ok(svg.includes('<desc>' + url.replace(/&/g, '&amp;') + '</desc>'));
  assert.doesNotMatch(svg, /<script|onload=|<foreignObject|<image/i);
});
