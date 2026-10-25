// SPDX-License-Identifier: GPL-3.0-only
// Isolated hypothesis test, never a default frontend or release update.
const fs=require('node:fs');
const crypto=require('node:crypto');
const {build}=require('./build-webcodecs-live-diagnostics');
if(process.argv.length!==3) throw new Error('NEW_HTML_FILE required');
const raw=Buffer.from(build({audCompatibility:true}));
fs.writeFileSync(process.argv[2],raw,{flag:'wx',mode:0o600});
console.log(JSON.stringify({compatibilityTrial:'omit-aud',bytes:raw.length,
  sha256:crypto.createHash('sha256').update(raw).digest('hex')}));
