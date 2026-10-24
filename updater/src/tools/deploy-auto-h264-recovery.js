// SPDX-License-Identifier: GPL-3.0-only
// Fixed code-only .90 deployment. No service restart or preference restoration.
const fs = require('node:fs/promises'), path = require('node:path'), crypto = require('node:crypto');
const HOME = '/home/hilman/teslaandroid-waydroid';
const STAGE = HOME + '/auto-h264-stage-20261010';
const FRONTEND = HOME + '/frontend';
const BACKUP = '/var/lib/teslaandroid-waydroid/backups/auto-h264-recovery-20261010';
const FILES = [
  { file: 'js/display/display-renderer-manager.js',
    before: 'ccb7077fd0ede6ff352b36328f49629e874e80d41cc22bf3efc546565c93ddcf',
    after: '444f6338acad614ee7ef4a1e9b95ae7780227e647f3f83c82bee36003b20d7de' },
  { file: 'js/display/renderers/h264-webcodecs/h264WebCodecs.js',
    before: '1577e735cfb5626b40e6cdab6f13f48caadedc7c39a09adf3b669acd8bb5b9a2',
    after: '2a9d0e2f41dc06e821a1cac2ec7d6f3920be214716c70465b4628c5173bafb69' },
];
const STARTUP_FILES = [{ file: 'js/display/display-controller.js',
  before: 'f35661d49ce22cdde72e689905cd753fd7730aca103388f7f20ebe42299e93f1',
  after: 'eaa5d663241b6198dc07cec3a932778a01ec299bcd78142894e31698f5cca96c' }];
const LEGACY_DEFAULT_FILES = [{ file: 'js/display/display-renderer-manager.js',
  before: '444f6338acad614ee7ef4a1e9b95ae7780227e647f3f83c82bee36003b20d7de',
  after: 'b4696739375bd1ddf45440c527d3cd38545028f7c0f0ed1ee1bba7abd15d5f17' }];
const SUPPORTED_ASPECT_FILES = [{ file: 'js/display/display-controller.js',
  before: 'eaa5d663241b6198dc07cec3a932778a01ec299bcd78142894e31698f5cca96c',
  after: '6c5419c48374d847d204374e0ae6dfb55039e0f3a41064832e6f4363b308f8e2' }];
const CAPTURE_PREROLL_FILES = [{ file: 'runtime/preview-gateway/compositor-h264.py',
  before: 'b9e4be47f457d3f705accdf3a7dd90110f2bf88d482fe6c025f2cd6b755c6ab2',
  after: 'aec38fad10103aed699b7efebefd8665bdc9159248dc688335d1c206d36d3ad7' }];
const CAPTURE_REPAINT_FILES = [{ file: 'runtime/preview-gateway/compositor-video-launcher.js',
  before: 'a55828d7e361d820891d0d74f392558bddb4569671e38a1a4127bde3ffa9e7e6',
  after: '0b1d6f08c0ac6afbe85781c23238fbd57db4dc0ff70230cf41ff132c211edc86' }];
const CAPTURE_BACKPRESSURE_FILES = [{ file: 'runtime/preview-gateway/compositor-h264.py',
  before: 'b9e4be47f457d3f705accdf3a7dd90110f2bf88d482fe6c025f2cd6b755c6ab2',
  after: '086ece12aaea13515c77719c6332ca42a955807f2a47f4c196636f8c8af87136' }];
const CAPTURE_RATE_LIMIT_FILES = [{ file: 'runtime/preview-gateway/compositor-h264.py',
  before: '086ece12aaea13515c77719c6332ca42a955807f2a47f4c196636f8c8af87136',
  after: '63f8e631cc31822545ffca5160a46fb72e87d11f908fc4eb4d23e0e64db0d08e' }];
const AUDIO_DELAY_FILES = [
  { file: 'js/audio/audioplayback.js',
    before: '716ed4a0a0f0144e6fa800f8d3d97233eee256203a4ed7839e0531f00206c63e',
    after: '7092f84c513f140447ebb9dcaf5152986b4f10ab24ea9a0d88ccfb7ea717a307' },
  { file: 'js/settings/settings-controller.js',
    before: 'c1514d8ef0233c3cd56689efedce9dd8c0d94bcdc165a957c94eb5a6e259fcad',
    after: 'a939a150665faa7cbc730b88a7b949946d6adca6e259ee64b9b5ef801c16567d' },
];
const AUDIO_DELAY_GUARD_FILES = [{ file: 'js/audio/audioplayback.js',
  before: AUDIO_DELAY_FILES[0].after,
  after: '4c18cf01f9d7a1d85b99cb9d2aa326a9ec649e3af57d057db7ec00f548c4e7a7' }];
const NETWORK_RUNTIME_FILES = [
  { file: 'runtime/preview-gateway/device-info.js',
    before: '05a84338fd1a63240d4a40a4def7fd1d1d826a2cc17289631210c0e1c67f6e69',
    after: 'e15c436b1787bc17aa4cd7c3646d30720a2c114e3b661159fd9201cf1a030fbd' },
  { file: 'runtime/preview-gateway/server.js',
    before: '382cb175378a9600462305008fc6ca88da8a8513664f13cdd4273c0d6dfe878f',
    after: '3eaab1b4b1d0f76ec398ca68eb90b2c791f3ef3e50b75b17f4c1183fa7ed037b' },
];
const NETWORK_FRONTEND_FILES = [
  { file: 'css/app.css',
    before: 'd8975198518c15562e2d240e4b9e90f68aae454ded196bd018e634b4fe4d77b8',
    after: 'f016d55141bca95ec81fac95d5a5da3ef3d10c1a166307a2f54f4ac9372c76ab' },
  { file: 'js/settings/settings-controller.js',
    before: AUDIO_DELAY_FILES[1].after,
    after: 'e36e138f1db164a501fda08e05beb0482123b8cfc93829648a0019fe51cfae33' },
];
const AV_SYNC_SIGNED_FILES = [
  { file: 'js/audio/audioplayback.js', before: AUDIO_DELAY_GUARD_FILES[0].after,
    after: 'd76d86d1930d337edd7054f16e5c6281c3fab558a9ad26623290c3df7fb138c1' },
  { file: 'js/display/renderers/h264-broadway/h264Brodway.js',
    before: 'a42d5e5a1b15383c3d96a40fc14d63d03dd4f789769ef58eb628c6ad7f58b1d6',
    after: '223dafda0847e4b1ee50579c9f0046c6554744898ba6b035ec258c635d9a39b7' },
  { file: 'js/display/renderers/h264-webcodecs/h264WebCodecs.js',
    before: '2a9d0e2f41dc06e821a1cac2ec7d6f3920be214716c70465b4628c5173bafb69',
    after: 'ba44f732c3e9ae03bff9b162150f52f32709c26349c809a7cb97bd65b384d32a' },
  { file: 'js/settings/settings-controller.js', before: NETWORK_FRONTEND_FILES[1].after,
    after: '56709feb6dbee943a5dadc03b31cdf8d37aab6293dfe35be6d6ed1cf5726fa64' },
];
const SETTINGS_CLEANUP_FILES = [
  { file: 'css/app.css', before: NETWORK_FRONTEND_FILES[0].after,
    after: '59211a32f902170b3a3d94a9cc91111448b93b239d012a726bf912eeed62183f' },
  { file: 'js/settings/settings-controller.js', before: AV_SYNC_SIGNED_FILES[3].after,
    after: '34c98c1e702fb64d645e2df2cbddd4b2d8cc3ec832e64645b8d36194f454ac58' },
];
const PROJECT_UPDATES_FILES = [{ file: 'js/core/api-client.js',
  before: '04fb78866c9976c731fde737683104097e3f3c171a2cf23c74792ecedb520663',
  after: '8299e34eea9145b735614a57b158fdac08aa31e925315da49025067a25a3f97a' }];
// Publish the new asset first and its HTML references last.
const DROIDDECK_BRAND_FILES = [
  { file: 'assets/droiddeck-logo.png', before: null,
    after: '6dfbe17b8b505c1bd5a334c067c96d4f1a7d0d00390b6a5f163d1fa2547dbc30' },
  { file: 'css/app.css', before: SETTINGS_CLEANUP_FILES[0].after,
    after: 'f0d2281b1639bc46a16b5aa2841998dcacb6a0a838ff4e7e64c15599cde00f8a' },
  { file: 'js/core/shared.js', before: 'f2f608976e20002c034455599bffce15e3463cdac1f112f16abb66576edd385b',
    after: 'eae21e85c904e3768bd29d1cfec7c16dcbff01d03ddc4cae53bbf0c6311f0253' },
  { file: 'js/shell/shell-controller.js', before: '778c492ce61b464e5f42874ef17b970e6c4ff36b271e0b629b4ebbd2bcca1ada',
    after: '54bf4912e479a83ae2133d851f8f278af972b5aef6c72cd47b7306014b639828' },
  { file: 'js/audio/audioplayback.js', before: AV_SYNC_SIGNED_FILES[0].after,
    after: '3732490a5dc9f4f6afa4327f147819184735edd29775504c8462f9d37fdb8754' },
  { file: 'js/release-notes/release-notes-data.js', before: 'bf91ddbc15221299fac557057d3c6907e0fb60160e2c9c435c0090d94d8bf00d',
    after: '720d1f9a11b2b94a201c925158a00429f9de5b24a3a03698a62cd7bd8f87930b' },
  { file: 'index.html', before: '54e319600d9de86535e0b892080841843546ba60ae8bce10b5e45c6e4ae9ff02',
    after: '5e615921161cb5bbad615db24f0d3212ffb607158fb8f3e669a6649577c4c8d5' },
];
const DROIDDECK_DONATION_FILES = [
  { file: 'assets/donations-qr.svg', before: 'f7ea47b4e6347db7085611f68668a8a7db6716fed6dcdc9d2affa18fe3fe79c2',
    after: '1935564279df3fa85e4fa93f4288d19a05d1b3c27f61ae6572039773f7deeee0' },
  { file: 'index.html', before: DROIDDECK_BRAND_FILES[6].after,
    after: 'a1014c275bf5577afdf13aded1e37b537dd50788b02b53b4af5e818eb8ca6fa1' },
];
const BITRATE_3_RUNTIME_FILES = [
  { file: 'runtime/preview-gateway/video-settings.js', before: 'd701684f6c4b222b671f3f06a094c65a4887a224670cfc536b2d56f550ed87dd',
    after: '73cb85b2b7a103f549b74a95fb4e7ef6152bb8c917f3603189e803b741604767' },
  { file: 'runtime/preview-gateway/compositor-h264.py', before: CAPTURE_RATE_LIMIT_FILES[0].after,
    after: '674ebfe44b9e20707f87d45f0f5cbf2283f445c174042290dda8d2eb19abe40b' },
];
const BITRATE_3_FRONTEND_FILES = [{ file: 'js/settings/settings-controller.js', before: SETTINGS_CLEANUP_FILES[1].after,
  after: '252df5884d6bf7b0a65d55fff8511e8e551875c04439fc6dccc44699f7f05bd4' }];
const CONNECTION_RUNTIME_FILES = [
  { file: 'runtime/browser-connection/policy.js', before: null,
    after: 'e71f0bb2b3193716d40a9d52bd64334265d79c499e6c9531d9f06a79f9306cbb' },
  { file: 'runtime/browser-connection/proxy-store.js', before: null,
    after: '90139347d5f8559fa092b8c800526618d334bf3cccc1e5eff53bd6f307fcffa7' },
  { file: 'runtime/browser-connection/helper.js', before: null,
    after: 'eaaef10bb917e8c4b88508a635809784d8a00928237e68f2cd7e87f54870d55a' },
  { file: 'runtime/preview-gateway/server.js', before: NETWORK_RUNTIME_FILES[1].after,
    after: '047d4221162709b8cdc62e30879f50432130bc3b5bd5a89907c37b0524cac848' },
];
const CONNECTION_FRONTEND_FILES = [
  { file: 'js/core/api-client.js', before: PROJECT_UPDATES_FILES[0].after,
    after: '5065a0cbf131ab4845a71912d5c457f95a63ebc93b3b5e91a8fd4140efd93c1b' },
  { file: 'js/settings/settings-controller.js', before: BITRATE_3_FRONTEND_FILES[0].after,
    after: 'b00afc039f2aa439287e8d5f6043021cada6a79f33556bdcabf366d22ddb1d40' },
  { file: 'css/app.css', before: 'f0d2281b1639bc46a16b5aa2841998dcacb6a0a838ff4e7e64c15599cde00f8a',
    after: '2596687856957f062eb8a5a3577e038d295abaac83c684d8e72922bef4be37d8' },
];
// Install the ACK-capable worker before its versioned, bounded-inbox sender.
const LEGACY_BACKPRESSURE_FILES = [
  { file: 'js/display/renderers/h264-broadway/h264BrodwayWorker.js',
    before: '8bf8a3e7701b836ea2a5153523811b07d673683bc99136e6a504ffe8ebce56d3',
    after: 'f34045ad9d0c10aed19bb9c5d2e621dd96779d56c74e5040e338a09409acb280' },
  { file: 'js/display/renderers/h264-broadway/h264Brodway.js',
    before: 'ed2038355dfc8e1fd3279e4d7b17ad593955a36676284a8747325fbadc0fb7d2',
    after: 'a42d5e5a1b15383c3d96a40fc14d63d03dd4f789769ef58eb628c6ad7f58b1d6' },
];
function deploymentProfile(args) {
  if (args[0] === '--droiddeck-logo') {
    if (args.length > 2 || (args[1] && args[1] !== '--rollback')) throw new Error('Invalid deployment profile');
    return { root: FRONTEND, stage: HOME + '/logo-stage-20261017fb',
      backup: '/var/lib/teslaandroid-waydroid/backups/logo-refresh-20261017fb', rollback: args[1] === '--rollback',
      files: [{ file: 'assets/droiddeck-logo.png',
        before: '6dfbe17b8b505c1bd5a334c067c96d4f1a7d0d00390b6a5f163d1fa2547dbc30',
        after: 'ea8e12af127eba9bc533d805770e06e3da9f7a0f277f36a2eac574c363fdeb29' },
      { file: 'css/app.css', before: '4af5930954a527f41c4b6a1d3ae263b8618ebd4be4d1369b62c8661af79e6b95',
        after: '81e0cd8fdcb17fd94c2e938b309adca674959d21e562bcf57fa97435e15f42e1' }] };
  }
  if (args[0] === '--settings-menu-frontend') {
    if (args.length > 2 || (args[1] && args[1] !== '--rollback')) throw new Error('Invalid deployment profile');
    return { root: FRONTEND, stage: HOME + '/settings-menu-stage-20261013as',
      backup: '/var/lib/teslaandroid-waydroid/backups/settings-menu-frontend-20261013as', rollback: args[1] === '--rollback',
      files: [{ file: 'css/app.css', before: '2596687856957f062eb8a5a3577e038d295abaac83c684d8e72922bef4be37d8',
        after: '4af5930954a527f41c4b6a1d3ae263b8618ebd4be4d1369b62c8661af79e6b95' },
      { file: 'js/settings/settings-controller.js', before: '296b1c359617698c8b3323173073ada45e13aef70e1b0f119c973a7fa9b1634f',
        after: 'aab0751ac4906f8295ef162379b5d65f793389faf589a799ce0aa4d9e9d9198a' }] };
  }
  if (args[0] === '--live-window-runtime' || args[0] === '--live-window-frontend') {
    if (args.length > 2 || (args[1] && args[1] !== '--rollback')) throw new Error('Invalid deployment profile');
    const runtime = args[0] === '--live-window-runtime';
    return { root: runtime ? '/opt/teslaandroid-waydroid' : FRONTEND,
      stage: HOME + '/live-window-stage-20261013ap',
      backup: '/var/lib/teslaandroid-waydroid/backups/live-window-' + (runtime ? 'runtime' : 'frontend') + '-20261013ap',
      rollback: args[1] === '--rollback', files: runtime ? [{ file: 'runtime/preview-gateway/h264-relay.js',
        before: '7b547ce2d4794ad890c66170126ca784936c721b857b7431aa8b1feee05c17d1',
        after: 'c6ee510353c297106641572bb1e61b0aa85fceb93b5524212cfd0b40975bd272' }] : [{ file: 'js/display/display-renderer-manager.js',
        before: 'b4696739375bd1ddf45440c527d3cd38545028f7c0f0ed1ee1bba7abd15d5f17',
        after: 'd086e1f915e01d80520e6b0d71dc4fa05614b5b52e17a16bce584cff8ef38527' }] };
  }
  if (args[0] === '--display-role-startup') {
    if (args.length > 2 || (args[1] && args[1] !== '--rollback')) throw new Error('Invalid deployment profile');
    return { root: FRONTEND, stage: HOME + '/display-role-startup-stage-20261012t2',
      backup: '/var/lib/teslaandroid-waydroid/backups/display-role-startup-20261012t2', rollback: args[1] === '--rollback',
      files: [{ file: 'js/display/display-controller.js', before: '07c4c2a6fefb77e187d51d480e96fcd32c7d5a39d4f8e3856a299a5fec4a463b', after: '622deb093c65f3b9a24f0fa1ced9d3e67d83ea71e87b45c694fab443dd12e67b' }] };
  }
  if (args[0] === '--display-role-runtime' || args[0] === '--display-role-frontend') {
    if (args.length > 2 || (args[1] && args[1] !== '--rollback')) throw new Error('Invalid deployment profile');
    const runtime = args[0] === '--display-role-runtime';
    return { root: runtime ? '/opt/teslaandroid-waydroid' : FRONTEND,
      stage: HOME + '/display-role-stage-20261012t',
      backup: '/var/lib/teslaandroid-waydroid/backups/display-role-' + (runtime ? 'runtime' : 'frontend') + '-20261012t',
      files: runtime ? [
        { file: 'runtime/preview-gateway/display-control.js', before: '24c56d4026ba65250a74de996d474de8c4dbb5f6cd60601bf4b6ca56aa171bf9', after: '7d0c3d953b07c18120f13b9acd076ce416cc98b2572bcbbd41697a8ca23a3c90' },
        { file: 'runtime/preview-gateway/server.js', before: '047d4221162709b8cdc62e30879f50432130bc3b5bd5a89907c37b0524cac848', after: '7f3b3dfa88842fde4c52b7734da865f262da91b46da717a9d3fd0e8fd487d266' },
      ] : [
        { file: 'js/core/api-client.js', before: '5065a0cbf131ab4845a71912d5c457f95a63ebc93b3b5e91a8fd4140efd93c1b', after: '95e5236908f962ede07a679cc9ff7f24fcbe63cacc6c1d7091deff30e0efda2c' },
        { file: 'js/display/display-controller.js', before: '6c5419c48374d847d204374e0ae6dfb55039e0f3a41064832e6f4363b308f8e2', after: '07c4c2a6fefb77e187d51d480e96fcd32c7d5a39d4f8e3856a299a5fec4a463b' },
        { file: 'js/settings/settings-controller.js', before: 'b00afc039f2aa439287e8d5f6043021cada6a79f33556bdcabf366d22ddb1d40', after: '296b1c359617698c8b3323173073ada45e13aef70e1b0f119c973a7fa9b1634f' },
      ], rollback: args[1] === '--rollback' };
  }
  if (args[0] === '--browser-connection-runtime' || args[0] === '--browser-connection-frontend') {
    if (args.length > 2 || (args[1] && args[1] !== '--rollback')) throw new Error('Invalid deployment profile');
    const runtime = args[0] === '--browser-connection-runtime';
    return { root: runtime ? '/opt/teslaandroid-waydroid' : FRONTEND,
      stage: HOME + '/browser-connection-stage-20261012c2',
      backup: '/var/lib/teslaandroid-waydroid/backups/browser-connection-' + (runtime ? 'runtime' : 'frontend') + '-20261012c',
      files: runtime ? CONNECTION_RUNTIME_FILES : CONNECTION_FRONTEND_FILES, rollback: args[1] === '--rollback' };
  }
  if (args[0] === '--bitrate-3-runtime' || args[0] === '--bitrate-3-frontend') {
    if (args.length > 2 || (args[1] && args[1] !== '--rollback')) throw new Error('Invalid deployment profile');
    const runtime = args[0] === '--bitrate-3-runtime';
    return { root: runtime ? '/opt/teslaandroid-waydroid' : FRONTEND,
      stage: HOME + '/bitrate-3-stage-20261012a',
      backup: '/var/lib/teslaandroid-waydroid/backups/bitrate-3-' + (runtime ? 'runtime' : 'frontend') + '-20261012a',
      files: runtime ? BITRATE_3_RUNTIME_FILES : BITRATE_3_FRONTEND_FILES, rollback: args[1] === '--rollback' };
  }
  if (args[0] === '--droiddeck-donation') {
    if (args.length > 2 || (args[1] && args[1] !== '--rollback')) throw new Error('Invalid deployment profile');
    return { root: FRONTEND, stage: HOME + '/droiddeck-donation-stage-20261011z',
      backup: '/var/lib/teslaandroid-waydroid/backups/droiddeck-donation-20261011z',
      files: DROIDDECK_DONATION_FILES, rollback: args[1] === '--rollback' };
  }
  if (args[0] === '--droiddeck-brand') {
    if (args.length > 2 || (args[1] && args[1] !== '--rollback')) throw new Error('Invalid deployment profile');
    return { root: FRONTEND, stage: HOME + '/droiddeck-brand-stage-20261011y',
      backup: '/var/lib/teslaandroid-waydroid/backups/droiddeck-brand-20261011y',
      files: DROIDDECK_BRAND_FILES, rollback: args[1] === '--rollback' };
  }
  if (args[0] === '--project-updates') {
    if (args.length > 2 || (args[1] && args[1] !== '--rollback')) throw new Error('Invalid deployment profile');
    return { root: FRONTEND, stage: HOME + '/project-updates-stage-20261011x',
      backup: '/var/lib/teslaandroid-waydroid/backups/project-updates-20261011x',
      files: PROJECT_UPDATES_FILES, rollback: args[1] === '--rollback' };
  }
  if (args[0] === '--settings-cleanup') {
    if (args.length > 2 || (args[1] && args[1] !== '--rollback')) throw new Error('Invalid deployment profile');
    return { root: FRONTEND, stage: HOME + '/settings-cleanup-stage-20261011w',
      backup: '/var/lib/teslaandroid-waydroid/backups/settings-cleanup-20261011w',
      files: SETTINGS_CLEANUP_FILES, rollback: args[1] === '--rollback' };
  }
  if (args[0] === '--av-sync-signed') {
    if (args.length > 2 || (args[1] && args[1] !== '--rollback')) throw new Error('Invalid deployment profile');
    return { root: FRONTEND, stage: HOME + '/av-sync-signed-stage-20261011v',
      backup: '/var/lib/teslaandroid-waydroid/backups/av-sync-signed-20261011v',
      files: AV_SYNC_SIGNED_FILES, rollback: args[1] === '--rollback' };
  }
  const startup = args[0] === '--startup';
  const legacy = args[0] === '--legacy-default';
  const aspects = args[0] === '--supported-aspects';
  const preroll = args[0] === '--capture-preroll';
  const repaint = args[0] === '--capture-repaint';
  const backpressure = args[0] === '--legacy-backpressure-v2';
  const captureBackpressure = args[0] === '--capture-backpressure';
  const rateLimit = args[0] === '--capture-rate-limit';
  const audioDelay = args[0] === '--audio-delay';
  const audioDelayGuard = args[0] === '--audio-delay-guard';
  const networkRuntime = args[0] === '--network-runtime-restore';
  const networkFrontend = args[0] === '--network-frontend-restore';
  const rest = startup || legacy || aspects || preroll || repaint || backpressure || captureBackpressure || rateLimit || audioDelay || audioDelayGuard || networkRuntime || networkFrontend ? args.slice(1) : args;
  if (rest.length > 1 || (rest[0] && rest[0] !== '--rollback')) throw new Error('Invalid deployment profile');
  return { root: preroll || repaint || captureBackpressure || rateLimit || networkRuntime ? '/opt/teslaandroid-waydroid' : FRONTEND,
    stage: networkRuntime || networkFrontend ? HOME + '/network-restore-stage-20261011t' : audioDelayGuard ? HOME + '/audio-delay-guard-stage-20261011s6' : audioDelay ? HOME + '/audio-delay-stage-20261011s' : rateLimit ? HOME + '/stream-cadence-rate-stage-20261011q2' : captureBackpressure ? HOME + '/stream-cadence-stage-20261011q' : backpressure ? HOME + '/legacy-backpressure-stage-20261011p2v2' : repaint ? HOME + '/capture-repaint-stage-20261010' :
      preroll ? HOME + '/capture-preroll-stage-20261010' : aspects ? HOME + '/supported-aspects-stage-20261010' :
      legacy ? HOME + '/legacy-default-stage-20261010' : startup ? HOME + '/role-startup-stage-20261010' : STAGE,
    backup: networkRuntime ? '/var/lib/teslaandroid-waydroid/backups/network-runtime-restore-20261011t' : networkFrontend ? '/var/lib/teslaandroid-waydroid/backups/network-frontend-restore-20261011t' : audioDelayGuard ? '/var/lib/teslaandroid-waydroid/backups/audio-delay-guard-20261011s6' : audioDelay ? '/var/lib/teslaandroid-waydroid/backups/audio-delay-20261011s' : rateLimit ? '/var/lib/teslaandroid-waydroid/backups/capture-rate-limit-20261011q2' : captureBackpressure ? '/var/lib/teslaandroid-waydroid/backups/capture-backpressure-20261011q' : backpressure ? '/var/lib/teslaandroid-waydroid/backups/legacy-backpressure-20261011p2' : repaint ? '/var/lib/teslaandroid-waydroid/backups/capture-repaint-20261010' :
      preroll ? '/var/lib/teslaandroid-waydroid/backups/capture-preroll-20261010' :
      aspects ? '/var/lib/teslaandroid-waydroid/backups/supported-aspects-20261010' :
      legacy ? '/var/lib/teslaandroid-waydroid/backups/legacy-default-20261010' :
      startup ? '/var/lib/teslaandroid-waydroid/backups/role-startup-recovery-20261010' : BACKUP,
    files: networkRuntime ? NETWORK_RUNTIME_FILES : networkFrontend ? NETWORK_FRONTEND_FILES : audioDelayGuard ? AUDIO_DELAY_GUARD_FILES : audioDelay ? AUDIO_DELAY_FILES : rateLimit ? CAPTURE_RATE_LIMIT_FILES : captureBackpressure ? CAPTURE_BACKPRESSURE_FILES : backpressure ? LEGACY_BACKPRESSURE_FILES : repaint ? CAPTURE_REPAINT_FILES : preroll ? CAPTURE_PREROLL_FILES : aspects ? SUPPORTED_ASPECT_FILES : legacy ? LEGACY_DEFAULT_FILES : startup ? STARTUP_FILES : FILES,
    rollback: rest[0] === '--rollback' };
}
const sha = data => crypto.createHash('sha256').update(data).digest('hex');
function validateArtifact(data, expected) {
  if (!Buffer.isBuffer(data) || data.length > 1024 * 1024 || sha(data) !== expected) {
    throw new Error('Artifact checksum mismatch');
  }
}
async function protectedDirectory(dir) {
  const stat = await fs.lstat(dir);
  if (!stat.isDirectory() || stat.uid !== 0 || (stat.mode & 0o022)) throw new Error('Unsafe code directory');
}
async function readArtifact(filename, expected, protectedFile) {
  const stat = await fs.lstat(filename);
  if (!stat.isFile() || stat.nlink !== 1 || stat.size > 1024 * 1024 ||
    (protectedFile && (stat.uid !== 0 || (stat.mode & 0o022)))) throw new Error('Unsafe code artifact');
  const data = await fs.readFile(filename);
  validateArtifact(data, Array.isArray(expected) ? expected.find(hash => sha(data) === hash) : expected);
  return data;
}
async function replace(target, data) {
  const temp = target + '.auto-h264-20261010.tmp';
  await fs.writeFile(temp, data, { flag: 'wx', mode: 0o644 });
  await fs.chown(temp, 0, 0);
  await fs.chmod(temp, 0o644);
  await fs.rename(temp, target);
}
async function assertAbsent(target) {
  try { await fs.lstat(target); }
  catch (error) { if (error.code === 'ENOENT') return; throw error; }
  throw new Error('New artifact already exists');
}
async function publishNew(target, data) {
  const temp = target + '.droiddeck-brand-20261011y.tmp';
  await fs.writeFile(temp, data, { flag: 'wx', mode: 0o644 });
  try {
    await fs.chown(temp, 0, 0);
    await fs.chmod(temp, 0o644);
    // link refuses to replace even a symlink created after preflight.
    await fs.link(temp, target);
  } finally { await fs.unlink(temp); }
}
async function restoreAbsent(target, expected) {
  try { await fs.lstat(target); }
  catch (error) { if (error.code === 'ENOENT') return; throw error; }
  await readArtifact(target, expected, true);
  await fs.unlink(target);
}
async function guardBitrateRollback(inspectServices, readProfile) {
  const states = (await inspectServices()).toString().trim().split('\n').filter(Boolean);
  if (states.length !== 2 || states.some(state => state !== 'inactive')) {
    throw new Error('Stop streaming runtime and gateway before backend rollback');
  }
  if (readProfile().bitRate === 3000000) throw new Error('Choose a legacy-compatible bitrate before backend rollback');
}
async function guardDisplayRoleRuntime(inspectServices) {
  const states = (await inspectServices()).toString().trim().split('\n').filter(Boolean);
  if (states.length !== 2 || states.some(state => state !== 'inactive')) throw new Error('Stop streaming parent and gateway before role backend changes');
}
async function restore(BACKUP, FILES, root = FRONTEND) {
  await protectedDirectory(BACKUP);
  for (const [index, item] of FILES.entries()) {
    const target = root + '/' + item.file;
    await protectedDirectory(path.dirname(target));
    if (item.before === null) {
      try { await fs.lstat(target); }
      catch (error) { if (error.code === 'ENOENT') continue; throw error; }
      await readArtifact(target, item.after, true);
    } else {
      await readArtifact(target, [item.before, item.after], true);
      await readArtifact(BACKUP + '/code-' + index, item.before, true);
    }
  }
  for (const [index, item] of [...FILES.entries()].reverse()) {
    if (item.before === null) await restoreAbsent(root + '/' + item.file, item.after);
    else await replace(root + '/' + item.file, await readArtifact(BACKUP + '/code-' + index, item.before, true));
  }
  await fs.writeFile(BACKUP + '/status.json', JSON.stringify({ phase: 'restored', runtimeRestarted: false }) + '\n', { mode: 0o600 });
}
async function main() {
  if (process.platform !== 'linux' || process.getuid?.() !== 0) throw new Error('Fixed root code deployment required');
  const { root, stage: STAGE, backup: BACKUP, files: FILES, rollback } = deploymentProfile(process.argv.slice(2));
  if (process.argv[2] === '--display-role-runtime') {
    await guardDisplayRoleRuntime(async () => (await require('node:util').promisify(require('node:child_process').execFile)('/usr/bin/systemctl',
      ['show', '--property=ActiveState', '--value', 'tesla-waydroid-runtime.service', 'tesla-waydroid-gateway-preview.service'],
      { timeout: 10000, maxBuffer: 4096 })).stdout);
  }
  if (rollback && process.argv[2] === '--bitrate-3-runtime') {
    const reader = root + '/' + FILES[0].file;
    await readArtifact(reader, [FILES[0].before, FILES[0].after], true);
    const { VIDEO_SETTINGS_FILE, readVideoSettings } = require(reader);
    await guardBitrateRollback(async () => {
      const { promisify } = require('node:util');
      const result = await promisify(require('node:child_process').execFile)('/usr/bin/systemctl',
        ['show', '--property=ActiveState', '--value', 'tesla-waydroid-runtime.service', 'tesla-waydroid-gateway-preview.service'],
        { timeout: 10000, maxBuffer: 4096 });
      return result.stdout;
    }, () => readVideoSettings(VIDEO_SETTINGS_FILE, { uid: 1000 }));
  }
  if (rollback) { await restore(BACKUP, FILES, root); return; }
  const records = [];
  for (const [index, item] of FILES.entries()) {
    const target = root + '/' + item.file;
    await protectedDirectory(path.dirname(target));
    const previous = item.before === null ? (await assertAbsent(target), null) : await readArtifact(target, item.before, true);
    const candidate = await readArtifact(STAGE + '/frontend/' + item.file, item.after, false);
    records.push({ item, index, target, previous, candidate });
  }
  await protectedDirectory(path.dirname(BACKUP));
  await fs.mkdir(BACKUP, { mode: 0o700 });
  for (const record of records) {
    if (record.previous !== null) await fs.writeFile(BACKUP + '/code-' + record.index, record.previous, { flag: 'wx', mode: 0o600 });
  }
  await fs.writeFile(BACKUP + '/manifest.json', JSON.stringify({ files: FILES, runtimeRestarted: false }) + '\n', { flag: 'wx', mode: 0o600 });
  await fs.writeFile(BACKUP + '/status.json', '{"phase":"installing"}\n', { flag: 'wx', mode: 0o600 });
  const installed = [];
  try {
    for (const record of records) {
      if (record.item.before === null) {
        await assertAbsent(record.target);
        await publishNew(record.target, record.candidate);
      } else {
        await readArtifact(record.target, record.item.before, true);
        await replace(record.target, record.candidate);
      }
      installed.push(record);
    }
    for (const record of records) await readArtifact(record.target, record.item.after, true);
    await fs.writeFile(BACKUP + '/status.json', JSON.stringify({ phase: 'installed', runtimeRestarted: false }) + '\n', { mode: 0o600 });
    console.log(JSON.stringify({ deployed: true, backup: BACKUP, runtimeRestarted: false }));
  } catch (error) {
    for (const record of installed.reverse()) {
      await readArtifact(record.target, record.item.after, true);
      if (record.previous === null) await restoreAbsent(record.target, record.item.after);
      else await replace(record.target, record.previous);
    }
    await fs.writeFile(BACKUP + '/status.json', '{"phase":"failed-restored"}\n', { mode: 0o600 });
    throw error;
  }
}
if (require.main === module) main().catch(() => {
  console.error('Fixed frontend deployment failed; inspect protected status'); process.exitCode = 1;
});
module.exports = { FILES, validateArtifact, deploymentProfile, replace, assertAbsent, publishNew, restoreAbsent, guardBitrateRollback, guardDisplayRoleRuntime };
