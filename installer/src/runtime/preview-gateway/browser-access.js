// SPDX-License-Identifier: GPL-3.0-only
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { promisify } = require("node:util");
const scrypt = promisify(crypto.scrypt);
const usernamePattern = /^[A-Za-z0-9_.@-]{1,64}$/;

function invalid(message, status = 400) {
  return Object.assign(new Error(message), { status });
}

function validState(value) {
  return value && value.version === 1 && typeof value.enabled === "boolean" &&
    (value.enabled ? usernamePattern.test(value.username) &&
      /^[0-9a-f]{32}$/.test(value.salt) && /^[0-9a-f]{64}$/.test(value.hash) :
      value.username === undefined && value.salt === undefined && value.hash === undefined);
}

function createBrowserAccess(filename) {
  let state = { version: 1, enabled: false };
  const stat = fs.lstatSync(filename, { throwIfNoEntry: false });
  if (stat) {
    if (!stat.isFile() || stat.size > 4096 || (process.platform !== "win32" && (stat.mode & 0o077))) {
      throw new Error("Browser access file must be a private regular file");
    }
    state = JSON.parse(fs.readFileSync(filename, "utf8"));
    if (!validState(state)) throw new Error("Invalid browser access state; refusing to reset login");
  }
  let token = crypto.randomBytes(32).toString("hex");
  let cachedCredential;
  let updating = false;
  const pendingVerifications = new Map();
  const cacheKey = crypto.randomBytes(32);

  return {
    snapshot() {
      return { enabled: state.enabled, username: state.enabled ? state.username : "", csrfToken: token };
    },
    async authorized(header) {
      if (!state.enabled) return true;
      if (typeof header !== "string" || header.length > 1024) return false;
      const match = /^Basic ([A-Za-z0-9+/]+={0,2})$/i.exec(header);
      if (!match) return false;
      const bytes = Buffer.from(match[1], "base64");
      const text = bytes.toString("utf8");
      if (!Buffer.from(text, "utf8").equals(bytes)) return false;
      const separator = text.indexOf(":");
      if (separator < 1 || text.slice(0, separator) !== state.username) return false;
      const password = text.slice(separator + 1);
      if (Buffer.byteLength(password) > 256) return false;
      const digest = crypto.createHmac("sha256", cacheKey).update(bytes).digest();
      if (cachedCredential && crypto.timingSafeEqual(digest, cachedCredential)) return true;
      const cacheId = digest.toString("hex");
      if (pendingVerifications.has(cacheId)) return pendingVerifications.get(cacheId);
      if (pendingVerifications.size >= 4) throw invalid("Login verification is busy; retry shortly", 503);
      const original = state;
      const verification = (async () => {
        const candidate = await scrypt(password, Buffer.from(original.salt, "hex"), 32);
        if (state !== original || !crypto.timingSafeEqual(candidate, Buffer.from(original.hash, "hex"))) return false;
        cachedCredential = digest;
        return true;
      })();
      pendingVerifications.set(cacheId, verification);
      try { return await verification; }
      finally { pendingVerifications.delete(cacheId); }
    },
    async update(payload, suppliedToken) {
      if (suppliedToken !== token) throw invalid("Refresh settings before saving", 403);
      if (updating) throw invalid("Another login change is being saved", 409);
      if (!payload || typeof payload.enabled !== "boolean" ||
          Object.keys(payload).some((key) => !["enabled", "username", "password"].includes(key))) {
        throw invalid("Invalid browser access settings");
      }
      let next = { version: 1, enabled: payload.enabled };
      if (payload.enabled) {
        if (payload.username === undefined && payload.password === undefined && state.enabled) {
          next = state;
        } else {
          if (typeof payload.username !== "string" || !usernamePattern.test(payload.username) ||
              typeof payload.password !== "string" || payload.password.length < 12 ||
              Buffer.byteLength(payload.password) > 256 || /[\x00-\x1f\x7f]/.test(payload.password)) {
            throw invalid("Choose a valid username and a password of at least 12 characters");
          }
          next.username = payload.username;
          next.salt = crypto.randomBytes(16).toString("hex");
        }
      }
      updating = true;
      const temporary = filename + "." + crypto.randomBytes(8).toString("hex") + ".tmp";
      try {
        if (next.enabled && next !== state) {
          next.hash = (await scrypt(payload.password, Buffer.from(next.salt, "hex"), 32)).toString("hex");
        }
        fs.mkdirSync(path.dirname(filename), { recursive: true, mode: 0o700 });
        fs.writeFileSync(temporary, JSON.stringify(next) + "\n", { mode: 0o600, flag: "wx" });
        fs.renameSync(temporary, filename);
        state = next;
        token = crypto.randomBytes(32).toString("hex");
        cachedCredential = undefined;
        return this.snapshot();
      } finally {
        updating = false;
        if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
      }
    },
  };
}

module.exports = { createBrowserAccess };
