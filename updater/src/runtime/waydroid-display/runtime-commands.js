// SPDX-License-Identifier: GPL-3.0-only
const { SERVICES } = require("./runtime-supervisor");
const { validateSize, parseBootClasspaths, GUEST_LIBS } = require("./host-adapter");
const { normalizeRuntimeConfig, deriveRuntimeLayout } = require('../portable/runtime-config');
const { configurationDigest } = require('../portable/configuration-generation');
const ROOT = "/opt/teslaandroid-waydroid", HOME = "/home/hilman/teslaandroid-waydroid";
const BASE = "/data/local/tmp/tesla-waydroid-test-20261003";
const NODE_PATH = ROOT + "/runtime/preview-gateway/node_modules:" + ROOT + "/runtime/waydroid-display/node_modules";
function runtimeUnit(name) {
  if (typeof name !== "string" || !SERVICES.includes(name)) throw new Error("Unknown runtime service");
  return "tesla-waydroid-" + name + "-preview";
}
function runtimeCommand(name, state, backend = process.env.TESLA_WAYDROID_CAPTURE_BACKEND, configuration) {
  if (backend !== undefined && backend !== 'compositor') throw new Error('Invalid capture backend');
  const config = configuration === undefined ? null : normalizeRuntimeConfig(configuration);
  const layout = config ? deriveRuntimeLayout(config) : null;
  const root = layout?.root || ROOT, user = config?.user || 'hilman', port = config?.port || 8443;
  const lanAddress = config?.lanAddress || '192.168.2.90';
  const nodePath = config ? root + '/runtime/preview-gateway/node_modules:' + root + '/runtime/waydroid-display/node_modules' : NODE_PATH;
  const socketRoot = layout?.sockets || '/run/teslaandroid-waydroid';
  const unit = runtimeUnit(name);
  if (!state || !Number.isSafeInteger(state.pid) || state.pid < 2) throw new Error("Invalid live container identity");
  validateSize(state);
  if (typeof state.containerAddress !== "string" || !/^192\.168\.240\.(?:[2-9]|[1-9]\d|1\d\d|2[0-4]\d|25[0-4])$/.test(state.containerAddress)) {
    throw new Error("Invalid Waydroid container address");
  }
  if (!Array.isArray(state.boot) || state.boot.length !== 2 || state.boot.some(value => typeof value !== "string")) throw new Error("Invalid boot environment");
  const boot = parseBootClasspaths(state.boot.map(value => "export " + value.replace("=", " ")).join("\n"));
  const hostLibs = root + "/libgbinder";
  const capture = ["video", "audio"].includes(name);
  const common = ["--unit=" + unit, "--property=Restart=" + (capture ? "always" : "on-failure"), "--property=RestartSec=2s",
    "--property=BindsTo=tesla-waydroid-runtime.service", "--property=After=tesla-waydroid-runtime.service",
    "--property=KillMode=control-group", "--property=TimeoutStopSec=60s", "--setenv=LD_LIBRARY_PATH=" + hostLibs,
    "--setenv=NODE_PATH=" + nodePath];
  const guest = ["/usr/bin/waydroid", "shell", "--", "/system/bin/env", "LD_LIBRARY_PATH=" + GUEST_LIBS];
  if (config) common.push('--setenv=DROIDDECK_CONFIGURATION_SHA256=' + configurationDigest(config));
  const entry = name => ['/usr/bin/node', root + '/runtime/portable/worker-entry.js', name];
  const native = role => {
    if (typeof state.nativeLease !== "string" || !/^[a-f0-9]{32}$/.test(state.nativeLease)) throw new Error("Native runtime lease required");
    return ["TESLA_WAYDROID_RUNTIME_OWNER=" + state.nativeLease, "TESLA_WAYDROID_RUNTIME_ROLE=" + role];
  };
  const namespace = ["/usr/bin/nsenter", "-t", String(state.pid), "-n", "/usr/sbin/runuser", "-u", user, "--", "/usr/bin/env", "NODE_PATH=" + nodePath];
  if (capture) common.push(...native(name).map(value => "--setenv=" + value));
  let worker;
  switch (name) {
    case "api": worker = [...guest, ...native("api"), BASE + "/bin/tesla-android-x86-api", String(state.width), String(state.height)]; break;
    case "display": worker = [...guest, ...boot, ...native("display"), BASE + "/bin/tesla-android-x86-display-latest", BASE + "/logical-screencap"]; break;
    case "display-helper": common.push("--setenv=TESLA_WAYDROID_SUPERVISED=1"); worker = config ? entry('display') : ["/usr/bin/node", root + "/runtime/waydroid-display/helper.js"]; break;
    case "gps": worker = config ? entry('gps') : ["/usr/bin/node", root + "/gps-stage/runtime/waydroid-display/gps-helper.js"]; break;
    case "video": common.push("--setenv=TESLA_WAYDROID_VIDEO_SETTINGS=1"); worker = config ? entry(backend === 'compositor' ? 'compositor-video' : 'video') : ["/usr/bin/node", root + "/runtime/preview-gateway/" + (backend === 'compositor' ? 'compositor-video-launcher.js' : 'video-launcher.js')]; break;
    case "audio": worker = config ? entry('audio') : ["/usr/bin/node", root + "/runtime/preview-gateway/audio-launcher.js"]; break;
    case "video-relay": worker = [...namespace, ...(config ? entry('video-relay') : ["/usr/bin/node", root + "/runtime/preview-gateway/h264-relay.js"])]; break;
    case "audio-relay": worker = [...namespace, "TESLA_WAYDROID_AUDIO_PCM_GAIN=1", ...(config ? entry('audio-relay') : ["/usr/bin/node", root + "/runtime/preview-gateway/audio-relay.js"])]; break;
    case "gateway": worker = [...namespace, "GATEWAY_ORIGIN=" + (layout?.gatewayOrigin || 'https://' + lanAddress + ':' + port), "GATEWAY_BIND=" + state.containerAddress,
      "GATEWAY_ANDROID_LOOPBACK=1", "GATEWAY_TLS_KEY_FILE=" + (layout?.keyFile || '/home/hilman/.config/teslaandroid-waydroid/key.pem'),
      "GATEWAY_TLS_CERT_FILE=" + (layout?.certificateFile || '/home/hilman/.config/teslaandroid-waydroid/cert.pem'),
      "GATEWAY_DISPLAY_SOCKET=" + socketRoot + "/display.sock", "GATEWAY_H264_STREAM=1", "GATEWAY_AUDIO_STREAM=1", "GATEWAY_VIDEO_SETTINGS=1", "GATEWAY_AUDIO_PCM_GAIN=1", "GATEWAY_DEVICE_INFO=1", "GATEWAY_REAR_DISPLAY=1",
      "GATEWAY_GPS_SOCKET=" + socketRoot + "/gps.sock", ...(config ? entry('gateway') : ["/usr/bin/node", root + "/runtime/preview-gateway/server.js", HOME + "/frontend", String(port)])]; break;
    case "lan-relay": common.push("--property=User=" + user); if (config) common.push('--property=Group=' + config.gid);
      worker = ["/usr/bin/socat", "TCP4-LISTEN:" + port + ",bind=" + lanAddress + ",reuseaddr,fork", "TCP4:" + state.containerAddress + ":" + port]; break;
  }
  return { file: "/usr/bin/systemd-run", args: [...common, ...worker] };
}
module.exports = { runtimeCommand, runtimeUnit, NODE_PATH, ROOT, HOME };
