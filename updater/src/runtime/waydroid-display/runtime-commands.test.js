// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { runtimeCommand, runtimeUnit } = require("./runtime-commands");
const { SERVICES } = require("./runtime-supervisor");
const state = { pid: 125574, width: 980, height: 720, density: 180, containerAddress: "192.168.240.112",
  nativeLease: "a".repeat(32),
  boot: ["BOOTCLASSPATH=/system/framework/framework.jar", "DEX2OATBOOTCLASSPATH=/apex/com.android.art/javalib/core-oj.jar"] };
test("only fixed project service names can select commands or unit names", () => {
  for (const name of SERVICES) { assert.match(runtimeUnit(name), /^tesla-waydroid-[a-z-]+-preview$/); assert.equal(runtimeCommand(name, state).file, "/usr/bin/systemd-run"); }
  for (const name of ["touch", "sshd", "../gateway", ["gps"], "gps;shutdown", ""]) {
    assert.throws(() => runtimeCommand(name, state)); assert.throws(() => runtimeUnit(name));
  }
});

test("capture units restart after normal source closure and receive fixed ownership tags", () => {
  for (const role of ["video", "audio"]) {
    const args = runtimeCommand(role, state).args;
    assert.ok(args.includes("--property=Restart=always"));
    assert.ok(args.includes("--setenv=TESLA_WAYDROID_RUNTIME_OWNER=" + state.nativeLease));
    assert.ok(args.includes("--setenv=TESLA_WAYDROID_RUNTIME_ROLE=" + role));
    assert.throws(() => runtimeCommand(role, { ...state, nativeLease: undefined }), /lease/);
  }
  assert.ok(runtimeCommand("video", state).args.includes("--setenv=TESLA_WAYDROID_VIDEO_SETTINGS=1"));
  assert.equal(runtimeCommand("audio", state).args.includes("--setenv=TESLA_WAYDROID_VIDEO_SETTINGS=1"), false);
});

test('compositor opt-in replaces video alone without changing audio, input or transport', () => {
  const next = runtimeCommand('video', state, 'compositor').args;
  assert.equal(next.at(-1), '/opt/teslaandroid-waydroid/runtime/preview-gateway/compositor-video-launcher.js');
  for (const name of SERVICES.filter(name => name !== 'video')) {
    assert.deepEqual(runtimeCommand(name, state, 'compositor'), runtimeCommand(name, state));
  }
  assert.throws(() => runtimeCommand('video', state, 'unknown'), /backend/);
});
test("namespace workers use the freshly supplied PID and current container IP, never saved unit text", () => {
  for (const name of ["video-relay", "audio-relay", "gateway"]) {
    const first = runtimeCommand(name, state).args, next = runtimeCommand(name, { ...state, pid: 222222 }).args;
    assert.equal(first[first.indexOf("-t") + 1], "125574"); assert.equal(next[next.indexOf("-t") + 1], "222222");
  }
  const gateway = runtimeCommand("gateway", { ...state, containerAddress: "192.168.240.113" }).args;
  assert.ok(gateway.includes("GATEWAY_BIND=192.168.240.113"));
  const relay = runtimeCommand("lan-relay", { ...state, containerAddress: "192.168.240.113" }).args;
  assert.ok(relay.includes("TCP4:192.168.240.113:8443"));
});

test("managed PCM gain flags reach only the unprivileged relay and gateway", () => {
  const relay = runtimeCommand("audio-relay", state).args, gateway = runtimeCommand("gateway", state).args;
  assert.ok(relay.includes("TESLA_WAYDROID_AUDIO_PCM_GAIN=1"));
  assert.ok(gateway.includes("GATEWAY_AUDIO_PCM_GAIN=1"));
  for (const args of [relay, gateway]) assert.ok(args.indexOf("/usr/sbin/runuser") < args.indexOf("/usr/bin/node"));
  for (const name of SERVICES.filter(name => !["audio-relay", "gateway"].includes(name))) {
    assert.equal(runtimeCommand(name, state).args.some(value => value.includes("AUDIO_PCM_GAIN")), false);
  }
});
test("public gateway retains TLS, dynamic H264 audio GPS and unprivileged UID without credentials in arguments", () => {
  const args = runtimeCommand("gateway", state).args;
  assert.ok(args.includes("GATEWAY_ORIGIN=https://192.168.2.90:8443"));
  for (const value of ["GATEWAY_ANDROID_LOOPBACK=1", "GATEWAY_H264_STREAM=1", "GATEWAY_AUDIO_STREAM=1", "GATEWAY_VIDEO_SETTINGS=1", "GATEWAY_DEVICE_INFO=1", "GATEWAY_GPS_SOCKET=/run/teslaandroid-waydroid/gps.sock", "GATEWAY_DISPLAY_SOCKET=/run/teslaandroid-waydroid/display.sock"]) assert.ok(args.includes(value));
  assert.deepEqual(args.slice(args.indexOf("/usr/sbin/runuser"), args.indexOf("/usr/sbin/runuser") + 4), ["/usr/sbin/runuser", "-u", "hilman", "--"]);
  assert.equal(args.some(value => /PASSWORD|TOKEN|--prepare/.test(value)), false);
});
test("guest display starts with validated live boot classpaths and logical capture wrapper", () => {
  const args = runtimeCommand("display", state).args;
  assert.ok(args.includes(state.boot[0])); assert.ok(args.includes(state.boot[1]));
  assert.equal(args.at(-1), "/data/local/tmp/tesla-waydroid-test-20261003/logical-screencap");
  assert.equal(runtimeCommand("api", state).args.slice(-2).join(" "), "980 720");
});

test("rear authority capability reaches only the session-coordinated gateway", () => {
  for (const name of SERVICES) assert.equal(runtimeCommand(name, state).args.includes("GATEWAY_REAR_DISPLAY=1"), name === "gateway");
});
test("invalid PID, size, classpaths or non-Waydroid IP cannot reach root launch", () => {
  for (const input of [{ ...state, pid: "2;kill" }, { ...state, pid: 1 }, { ...state, pid: 2.5 },
    { ...state, width: 0 }, { ...state, density: 1 }, { ...state, boot: ["BOOTCLASSPATH=/tmp/bad.jar", state.boot[1]] },
    { ...state, containerAddress: "127.0.0.1" }, { ...state, containerAddress: "192.168.2.90" }, { ...state, containerAddress: "192.168.240.112;cmd" }]) {
    assert.throws(() => runtimeCommand("gateway", input));
  }
});
test("all workers have bounded termination and protected root code, with touch owned only by helper", () => {
  for (const name of SERVICES) {
    const args = runtimeCommand(name, state).args;
    assert.ok(args.includes("--property=KillMode=control-group")); assert.ok(args.includes("--property=TimeoutStopSec=60s"));
    assert.ok(args.includes("--property=BindsTo=tesla-waydroid-runtime.service"));
    assert.ok(args.includes("--property=After=tesla-waydroid-runtime.service"));
    assert.equal(args.some(value => /runtime\/(?:waydroid-display|preview-gateway)\/.*\.js$/.test(value) && !value.startsWith("/opt/teslaandroid-waydroid/")), false);
  }
});

const portable = { user: 'deck', uid: 1200, gid: 1201, home: '/home/stream', lanAddress: '10.20.30.40', port: 9443, hostname: 'deck.example' };
test('unconfigured commands remain byte-identical to the accepted legacy generator', () => {
  const previous = process.env.TESLA_WAYDROID_CAPTURE_BACKEND; delete process.env.TESLA_WAYDROID_CAPTURE_BACKEND;
  try {
    const commands = JSON.stringify([undefined, 'compositor'].map(backend => SERVICES.map(name => runtimeCommand(name, state, backend))));
    assert.equal(require('node:crypto').createHash('sha256').update(commands).digest('hex'), '26f86ae3f7f855d2fcb4b340340a138b0f22c55ee38e3f713d743b119cc9772a');
  } finally { if (previous === undefined) delete process.env.TESLA_WAYDROID_CAPTURE_BACKEND; else process.env.TESLA_WAYDROID_CAPTURE_BACKEND = previous; }
});
test('explicit portable configuration replaces machine-specific host paths and LAN worker identity', () => {
  for (const name of SERVICES) {
    const command = runtimeCommand(name, state, 'compositor', portable);
    assert.doesNotMatch(JSON.stringify(command), /hilman|192\.168\.2\.90|\/opt\/teslaandroid-waydroid|\/home\/hilman|\/run\/teslaandroid-waydroid/);
    assert.ok(command.args.includes('--setenv=LD_LIBRARY_PATH=/opt/droiddeck/libgbinder'));
  }
  const gateway = runtimeCommand('gateway', state, 'compositor', portable).args;
  for (const value of ['GATEWAY_ORIGIN=https://10.20.30.40:9443', 'GATEWAY_TLS_KEY_FILE=/home/stream/.config/droiddeck/key.pem',
    'GATEWAY_TLS_CERT_FILE=/home/stream/.config/droiddeck/cert.pem', 'GATEWAY_DISPLAY_SOCKET=/run/droiddeck/display.sock',
    'GATEWAY_GPS_SOCKET=/run/droiddeck/gps.sock']) assert.ok(gateway.includes(value), value);
  assert.deepEqual(gateway.slice(-3), ['/usr/bin/node', '/opt/droiddeck/runtime/portable/worker-entry.js', 'gateway']);
  const namespace = gateway.indexOf('/usr/sbin/runuser'); assert.equal(gateway[namespace + 2], 'deck');
  const relay = runtimeCommand('lan-relay', { ...state, containerAddress: '192.168.240.113' }, 'compositor', portable).args;
  for (const value of ['--property=User=deck', '--property=Group=1201', 'TCP4-LISTEN:9443,bind=10.20.30.40,reuseaddr,fork', 'TCP4:192.168.240.113:9443']) assert.ok(relay.includes(value), value);
});
test('portable generation retains guest/lifecycle/ownership protocol and rejects invalid config', () => {
  const api = runtimeCommand('api', state, 'compositor', portable).args;
  assert.ok(api.includes('/data/local/tmp/tesla-waydroid-test-20261003/bin/tesla-android-x86-api'));
  assert.ok(api.includes('--property=BindsTo=tesla-waydroid-runtime.service'));
  assert.ok(runtimeCommand('video', state, 'compositor', portable).args.includes('--setenv=TESLA_WAYDROID_RUNTIME_OWNER=' + state.nativeLease));
  assert.equal(runtimeCommand('display', state, 'compositor', portable).args.at(-1), runtimeCommand('display', state).args.at(-1));
  for (const invalid of [{ ...portable, user: 'root' }, { ...portable, port: '9443' }, { ...portable, home: '/home/../root' }, { ...portable, password: 'secret' }]) assert.throws(() => runtimeCommand('gateway', state, 'compositor', invalid));
  for (const name of SERVICES) assert.deepEqual(runtimeCommand(name, state, 'compositor', undefined), runtimeCommand(name, state, 'compositor'));
});
