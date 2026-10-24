// SPDX-License-Identifier: GPL-3.0-only
const { normalizeRuntimeConfig, deriveRuntimeLayout, readRuntimeConfig } = require('./runtime-config');
const { configurationDigest } = require('./configuration-generation');
const GPU_SERVICE_GROUPS = Object.freeze(['render', 'video']);
function createServicePlan(input) {
  const config = normalizeRuntimeConfig(input), layout = deriveRuntimeLayout(config), digest = configurationDigest(config);
  const node = '/usr/bin/node ' + layout.root + '/runtime/portable/worker-entry.js ';
  const unit = (description, dependencies, service) => ['[Unit]', 'Description=DroidDeck ' + description,
    ...dependencies, 'StartLimitIntervalSec=60', 'StartLimitBurst=5', '', '[Service]', 'Type=simple',
    ...service, 'KillMode=control-group', 'UMask=0077', '', '[Install]', 'WantedBy=multi-user.target', ''].join('\n');
  const paths = ['Environment=NODE_PATH=' + layout.root + '/runtime/preview-gateway/node_modules:' + layout.root + '/runtime/waydroid-display/node_modules',
    'Environment=LD_LIBRARY_PATH=' + layout.hostLibraries, 'Environment=DROIDDECK_CONFIGURATION_SHA256=' + digest];
  const desktop = ['User=' + config.user, 'Group=' + config.gid, 'SupplementaryGroups=' + GPU_SERVICE_GROUPS.join(' '), 'Environment=HOME=' + config.home,
    'Environment=XDG_RUNTIME_DIR=' + layout.runtimeDirectory, 'Environment=DBUS_SESSION_BUS_ADDRESS=' + layout.dbusAddress,
    'Environment=WAYLAND_DISPLAY=wayland-teslaandroid', ...paths];
  const userManager = 'user@' + config.uid + '.service';
  const files = {
    'systemd/system/tesla-waydroid-compositor.service': unit('private GPU compositor',
      ['Requires=' + userManager, 'After=' + userManager], [...desktop, 'PAMName=droiddeck',
        'ExecStartPre=' + node + 'compositor-check',
        'ExecStart=/usr/bin/weston --backend=pipewire --renderer=gl --socket=wayland-teslaandroid --width=1280 --height=720 --idle-time=0 --no-config --shell=kiosk-shell.so',
        'Restart=on-failure', 'RestartSec=5s', 'TimeoutStopSec=20s']),
    'systemd/system/tesla-waydroid-headless-session.service': unit('unprivileged Android session',
      ['Requires=' + userManager + ' tesla-waydroid-compositor.service', 'Wants=waydroid-container.service',
        'After=' + userManager + ' tesla-waydroid-compositor.service waydroid-container.service',
        'PartOf=waydroid-container.service tesla-waydroid-compositor.service'], [...desktop,
        'ExecStartPre=' + node + 'headless-check', 'ExecStart=/usr/bin/waydroid show-full-ui',
        'Restart=on-failure', 'RestartSec=5s', 'KillSignal=SIGUSR1', 'TimeoutStartSec=45s', 'TimeoutStopSec=45s']),
    'systemd/system/tesla-waydroid-runtime.service': unit('runtime supervisor',
      ['After=network-online.target waydroid-container.service tesla-waydroid-headless-session.service droiddeck-browser-connection.service',
        'Wants=network-online.target waydroid-container.service tesla-waydroid-headless-session.service droiddeck-browser-connection.service'],
      ['User=root', ...paths, 'Environment=TESLA_WAYDROID_CAPTURE_BACKEND=compositor',
        'WorkingDirectory=' + layout.root + '/runtime/waydroid-display', 'ExecStart=' + node + 'runtime',
        'Restart=on-failure', 'RestartSec=2s', 'TimeoutStopSec=900s']),
    'systemd/system/droiddeck-browser-connection.service': unit('browser connection metadata helper',
      ['After=local-fs.target systemd-tmpfiles-setup.service'], ['User=root', 'Group=' + config.gid, ...paths,
        'ExecStart=' + node + 'connection', 'Restart=on-failure', 'RestartSec=2s', 'TimeoutStopSec=20s',
        'NoNewPrivileges=true', 'ProtectSystem=strict', 'ProtectHome=true', 'PrivateTmp=true',
        'ReadWritePaths=' + layout.state + '/browser-connection ' + layout.sockets,
        'RestrictAddressFamilies=AF_UNIX', 'ProtectKernelModules=true', 'ProtectControlGroups=true']),
    'tmpfiles.d/droiddeck.conf': 'd ' + layout.state + ' 0700 root root -\nd ' + layout.state + '/browser-connection 0700 root root -\nd ' + layout.sockets + ' 0750 root ' + config.gid + ' -\n',
    'pam.d/droiddeck': '#%PAM-1.0\naccount required pam_unix.so\nsession required pam_systemd.so class=background\n',
    'systemd/system/waydroid-container.service.d/50-droiddeck-session.conf': '[Unit]\nWants=tesla-waydroid-headless-session.service\n',
  };
  return Object.freeze({ kind: 'service-plan', installable: false, configurationDigest: digest, files: Object.freeze(files) });
}
if (require.main === module) {
  try {
    if (process.argv.length !== 4 || process.argv[2] !== 'inspect') throw new Error('Use inspect FILE');
    console.log(JSON.stringify(createServicePlan(readRuntimeConfig(process.argv[3])), null, 2));
  } catch (_) { console.error('Portable service plan rejected'); process.exitCode = 1; }
}
module.exports = { createServicePlan, GPU_SERVICE_GROUPS };
