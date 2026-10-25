// SPDX-License-Identifier: GPL-3.0-only
const { parseBootClasspaths } = require("./host-adapter");
const { normalizeLocation } = require("../preview-gateway/gps-location");
const GPS_JAR = "/data/local/tmp/tesla-waydroid-test-20261003/location-bridge.jar";
const GPS_HASH = "fa54d8a3e2ccb80c6e2c3b886d45445fb5eba66f3a43712ee81cbffeef0d062e";
const REPRODUCIBLE_GPS_HASH = "809acb4b5d4c86ece5d2378dc3dd9eb2b47bc4781d756012047570c61a493cbc";

function createLocationAdapter({ guest, expectedHash } = {}) {
  const hashes = expectedHash === undefined ? [GPS_HASH, REPRODUCIBLE_GPS_HASH] : [expectedHash];
  if (typeof guest !== "function" || hashes.some(hash => typeof hash !== 'string' || !/^[a-f0-9]{64}$/.test(hash))) throw new Error("Invalid location adapter");
  function lease(value) {
    if (typeof value !== "string" || !/^[a-f0-9]{32}$/.test(value)) throw new Error("Invalid location lease");
    return value;
  }
  async function request(operation, token, fix, options = {}) {
    lease(token); options.signal?.throwIfAborted();
    const ownership = (await guest(["/system/bin/toybox", "stat", "-c", "%u:%a", GPS_JAR], options)).toString().trim();
    const digest = (await guest(["/system/bin/toybox", "sha256sum", GPS_JAR], options)).toString().trim();
    if (!["0:600", "0:644"].includes(ownership) || !hashes.some(hash => digest === hash + "  " + GPS_JAR)) throw new Error("Unsafe location helper");
    const boot = parseBootClasspaths((await guest(["/system/bin/toybox", "cat", "/data/system/environ/classpath"], options)).toString());
    const output = await guest(["/system/bin/env", "CLASSPATH=" + GPS_JAR,
      "ANDROID_ART_ROOT=/apex/com.android.art", "ANDROID_I18N_ROOT=/apex/com.android.i18n",
      "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata", ...boot,
      "/system/bin/app_process", "/system/bin", "TeslaLocationBridge", operation, token],
      { ...options, ...(fix ? { input: JSON.stringify(fix) } : {}) });
    let state;
    try {
      if (output.length > 1024) throw new Error();
      state = JSON.parse(output.toString());
      if (!state || state.version !== 1 || typeof state.present !== "boolean" || typeof state.owned !== "boolean" ||
          (state.owned && !state.present) || Object.keys(state).length !== 3) throw new Error();
    } catch { throw new Error("Invalid location helper response"); }
    if (operation !== "--inspect" && (operation === "--close" ? state.present : !state.owned)) {
      throw new Error("Location provider conflict");
    }
    return { present: state.present, owned: state.owned };
  }
  return {
    inspect: (token, options) => request("--inspect", token, undefined, options),
    open: (token, options) => request("--open", token, undefined, options),
    close: (token, options) => request("--close", token, undefined, options),
    deliver(token, value, options) {
      try {
        lease(token);
        if (!value || typeof value !== "object" || Array.isArray(value) ||
            Object.keys(value).length !== 6 || Object.keys(value).some(key =>
              !["latitude", "longitude", "accuracy", "timestamp", "speed", "bearing"].includes(key))) throw new Error("Invalid location");
        const fix = normalizeLocation({ latitude: value.latitude, longitude: value.longitude, vertical_accuracy: value.accuracy,
          timestamp: value.timestamp, speed: value.speed, bearing: value.bearing });
        return request("--apply", token, fix, options);
      } catch (error) { return Promise.reject(error); }
    },
  };
}
module.exports = { createLocationAdapter, GPS_JAR, GPS_HASH, REPRODUCIBLE_GPS_HASH };
