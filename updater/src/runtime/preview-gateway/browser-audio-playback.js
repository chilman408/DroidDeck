// SPDX-License-Identifier: GPL-3.0-only
// Gateway-owned live audio player; protected frontend receipts remain unchanged.
(() => {
  const MIME_TYPES = { flac: 'audio/mp4; codecs="flac"', aac: 'audio/mp4; codecs="mp4a.40.2"' };
  const query = new URLSearchParams(window.location?.search || '');
  const QUALITY_KEY = 'DroidDeck_audioQuality_v1';
  let ceilingBitRate = 128000, automaticQuality = false;
  try {
    const saved = JSON.parse(window.localStorage?.getItem(QUALITY_KEY) || 'null');
    if (saved?.version === 1 && typeof saved.automatic === 'boolean' && [64000,128000,256000].includes(saved.bitRate)) {
      ceilingBitRate=saved.bitRate;automaticQuality=saved.automatic;
    }
  } catch (_) {}
  const override = query.getAll('audio').length === 1 ? query.get('audio') : null;
  if (override === 'aac64') ceilingBitRate=64000;
  else if (override === 'aac256') ceilingBitRate=256000;
  else if (override === 'aac') ceilingBitRate=128000;
  const requestedCodec = override === 'flac' ? 'flac' : 'aac';
  if (requestedCodec === 'flac') automaticQuality=false;
  let codec = requestedCodec, aacBitRate=automaticQuality ? Math.min(128000,ceilingBitRate) : ceilingBitRate;
  let requestedFormat=requestedCodec==='flac' ? 'flac' : ceilingBitRate===64000 ? 'aac64' : ceilingBitRate===256000 ? 'aac256' : 'aac';
  let fallbackReason = null, aacFailures = 0, aacProven = false;
  const announceCodec = () => { try { window.dispatchEvent(new CustomEvent('droiddeck-audio-format')); } catch (_) {} };
  const saveQuality = () => { try {window.localStorage?.setItem(QUALITY_KEY,JSON.stringify({version:1,automatic:automaticQuality,bitRate:ceilingBitRate}));}catch(_){} };
  window.DroidDeckAudio = Object.freeze({
    snapshot: () => ({ requested: requestedFormat, effective: codec, targetBitRate: codec === 'aac' ? aacBitRate : null,
      ceilingBitRate,automaticQuality,fallbackReason, playing: Boolean(audioEl && !audioEl.paused && audioEl.currentTime > 0 && !audioEl.error) }),
    select(value) {
      if (!['flac','aac','aac64','aac256'].includes(value)) throw new RangeError('Invalid audio format');
      const url = new URL(window.location.href); url.searchParams.delete('audio');
      if (value !== 'aac') url.searchParams.set('audio',value);
      if(value==='flac')automaticQuality=false;
      else {
        ceilingBitRate=value==='aac64'?64000:value==='aac256'?256000:128000;
        aacBitRate=automaticQuality?Math.min(aacBitRate,ceilingBitRate):ceilingBitRate;
      }
      requestedFormat=value;saveQuality();
      if(value!=='flac' && codec==='aac'){
        window.history?.replaceState(null,'',url.href);
        if(started)startWorker(window.audioWebsocketUrl);
        announceCodec();refreshMediaMetadata();return;
      }
      if (url.href === window.location.href) window.location.reload(); else window.location.replace(url.href);
    },
    setAutomatic(value) {
      if(typeof value!=='boolean')throw new RangeError('Automatic audio requires boolean');
      automaticQuality=value;saveQuality();
      if(codec!=='aac' && value){this.select('aac');return;}
      if(!value)aacBitRate=ceilingBitRate;
      if(started && codec==='aac')startWorker(window.audioWebsocketUrl);
      announceCodec();
    },
  });
  function aacFailure() {
    if (codec === 'aac' && !aacProven && ++aacFailures >= 3) {
      codec = 'flac'; fallbackReason = 'AAC playback failed; using FLAC.'; announceCodec(); return true;
    }
    return false;
  }

  const MAX_QUEUE_BYTES     = 512 * 1024;
  const MAX_QUEUE_AGE_MS    = 1500;
  let pendingAck = null;
  let transportEpoch = null, rebasePending = false, mseCodec = null;
  let userPaused = false;

  const TRIM_INTERVAL_MS    = 5000;
  const KEEP_TAIL_SEC       = 0.20;
  const MAX_LATENCY_SEC     = 1.0;
  const HARD_JUMP_SEC       = 0.10;
  const AUDIO_DELAY_KEY     = 'TAHtml_audioDelayMs';
  let audioDelayMs = 0;
  let delayedStartPending = false;
  let delayedStartPaused = false;
  let delayedStartTimer = null;
  const validDelay = value => Number.isInteger(value) && value >= -750 && value <= 750 && value % 25 === 0;
  try {
    const saved = Number(window.localStorage?.getItem(AUDIO_DELAY_KEY));
    if (validDelay(saved)) audioDelayMs = saved;
  } catch (_) {}

  // Hold encoded video, not decoded surfaces, when audio must lead the picture.
  window.createAVSyncVideoQueue = function(deliver, overflow) {
    let queue = [], bytes = 0, timer = null, generation = 0, closed = false, blocked = false;
    let delay = Math.max(0, -audioDelayMs);
    const now = () => performance.now();
    function cancel() { generation++; clearTimeout(timer); timer = null; }
    function drain() {
      cancel();
      if (closed || blocked) return;
      while (queue.length && queue[0].due <= now()) {
        const item = queue.shift(); bytes -= item.packet.byteLength; deliver(item.packet);
        if (closed || blocked) return;
      }
      if (queue.length) {
        const current = generation;
        timer = setTimeout(() => { if (current === generation) drain(); }, Math.max(0, queue[0].due - now()));
      }
    }
    function changed() {
      const next = Math.max(0, -audioDelayMs), delta = next - delay;
      delay = next; for (const item of queue) item.due += delta;
      drain();
    }
    window.addEventListener?.('ta-audio-sync-change', changed);
    return {
      push(packet) {
        if (closed || blocked) return;
        if (!delay && !queue.length) { deliver(packet); return; }
        // Room for the relay's complete cached GOP plus codec configuration.
        if (queue.length >= 128 || bytes + packet.byteLength > 2306868) {
          cancel(); queue = []; bytes = 0; blocked = true; overflow(); return;
        }
        queue.push({ packet, due: now() + delay }); bytes += packet.byteLength;
        drain();
      },
      reset() { cancel(); queue = []; bytes = 0; blocked = false; },
      close() { this.reset(); closed = true; window.removeEventListener?.('ta-audio-sync-change', changed); },
    };
  };

  let isAudioEnabled = true;
  let audioVolume = 1.0;

  let audioEl = null;
  let mediaSource = null;
  let sourceBuffer = null;

  let worker = null;
  let queue = [];
  let queueBytes = 0;
  let appending = false;

  let started = false;
  let trimmingTimer = null;
  let recovering = false;

  let audioCtx = null;
  let mediaUrl = null;
  let recoveryTimer = null;

  function log(){ /* console.log.apply(console, ['[Audio]', ...arguments]); */ }

  window.setAudioEnabled = function(enabled) {
    isAudioEnabled = (typeof enabled === 'boolean') ? enabled : (enabled !== 'false');
    if (!isAudioEnabled) window.stopAudioPlayback();
  };
  window.setAudioVolume = function(volume) {
    audioVolume = Number.isFinite(Number(volume)) ? Math.max(0, Math.min(1, Number(volume))) : 1;
    if (audioEl) audioEl.volume = audioVolume;
  };
  window.getAudioEnabled = function() { return isAudioEnabled; };
  window.getAudioDelayMs = function() { return audioDelayMs; };
  window.setAudioDelayMs = function(value) {
    if (!validDelay(value)) throw new RangeError('Invalid audio delay');
    if (value === audioDelayMs) return value;
    const previousAudioDelay = Math.max(0, audioDelayMs);
    audioDelayMs = value;
    try { window.localStorage?.setItem(AUDIO_DELAY_KEY, String(value)); } catch (_) {}
    if (typeof CustomEvent === 'function') window.dispatchEvent?.(new CustomEvent('ta-audio-sync-change'));
    if (started && previousAudioDelay !== Math.max(0, value)) { window.stopAudioPlayback(); window.startAudioPlayback(); }
    return value;
  };

  window.primeAudioFromGesture = function primeAudioFromGesture() {
    try {
      userPaused = false; ensureAudioEl();
      resumeAudioContextIfNeeded();
      refreshMediaMetadata(); refreshMetadataBurst();
      if (delayedStartPending) { delayedStartPaused = false; maybeStartDelayedAudio(); }
      else audioEl.play().catch(()=>{});
    } catch(_) {}
  };

  window.startAudioPlayback = function () {
    if (started || !isAudioEnabled) return;
    started = startAudio();
  };

  window.stopAudioPlayback = function () {
    started = false;
    clearTimeout(recoveryTimer); recoveryTimer = null; recovering = false;
    stopAudio();
  };

  function ensureAudioEl() {
    if (audioEl) return audioEl;

    audioEl = document.createElement('audio');
    audioEl.playsInline = true;
    audioEl.autoplay = true;
    audioEl.controls = false;
    audioEl.muted = false;
    audioEl.crossOrigin = "anonymous";
    audioEl.volume = Math.min(1, audioVolume);
    document.body.appendChild(audioEl);

    window.__liveAudioEl = audioEl;
    try { window.dispatchEvent(new CustomEvent('audio-el-created')); } catch {}

    audioEl.addEventListener('error', () => {
      recoverPipelineSoon();
    });
    audioEl.addEventListener('canplay', () => { maybeStartDelayedAudio(); if (started && isAudioEnabled) { refreshMediaMetadata();refreshMetadataBurst(); } });
    audioEl.addEventListener('playing', () => {
      if (started && isAudioEnabled && !userPaused) {
        refreshMediaMetadata();refreshMetadataBurst();
        try { navigator.mediaSession.playbackState = 'playing'; } catch (_) {}
      }
    });
    audioEl.addEventListener('timeupdate', () => {
      if (codec === 'aac' && !audioEl.paused && audioEl.currentTime > 0 && !audioEl.error) { aacProven = true; announceCodec(); }
    });

    return audioEl;
  }

  function ensureAudioContext() {
    if (audioCtx) return audioCtx;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    audioCtx = new Ctx();
    return audioCtx;
  }

  function resumeAudioContextIfNeeded() {
    const ctx = ensureAudioContext();
    if (ctx && ctx.state === 'suspended') {
      ctx.resume().catch(()=>{});
    }
  }

  function setupMSE() {
    mediaSource = new MediaSource();
    mseCodec = codec;
    const activeSource = mediaSource;
    const el = ensureAudioEl();
    mediaUrl = URL.createObjectURL(mediaSource);
    el.src = mediaUrl;
    el.load();

    mediaSource.addEventListener('sourceopen', () => {
      if (mediaSource !== activeSource) return;
      try { mediaSource.duration = Infinity; } catch(_){}
      try {
        sourceBuffer = mediaSource.addSourceBuffer(MIME_TYPES[codec]);
        sourceBuffer.mode = 'sequence';
        const activeBuffer = sourceBuffer;

        sourceBuffer.addEventListener('updateend', () => {
          if (mediaSource !== activeSource || sourceBuffer !== activeBuffer) return;
          appending = false;
          if (pendingAck !== null) {
            if (pendingAck.owner === worker && pendingAck.epoch === transportEpoch) {
              worker?.postMessage({ type: 'ack', id: pendingAck.id, epoch: pendingAck.epoch });
            }
            pendingAck = null;
          }
          // Seek before appending again: appendBuffer makes updating=true.
          maybeStartDelayedAudio();
          maybeReduceLatency();
          processQueue();
        });
        sourceBuffer.addEventListener('error', (e) => {
          if (mediaSource !== activeSource || sourceBuffer !== activeBuffer) return;
          log('SourceBuffer error', e);
          recoverPipelineSoon();
        });
        processQueue();
      } catch (e) {
        log('addSourceBuffer failed', e);
        recoverPipelineSoon();
        return;
      }

      if (trimmingTimer) clearInterval(trimmingTimer);
      trimmingTimer = setInterval(() => {
        maybeTrimOld();
        maybeReduceLatency();
      }, TRIM_INTERVAL_MS);
    });

    mediaSource.addEventListener('sourceended', () => log('MediaSource ended'));
    mediaSource.addEventListener('sourceclose', () => log('MediaSource closed'));
  }

  function destroyMSE() {
    try {
      if (sourceBuffer) {
        sourceBuffer.onupdateend = sourceBuffer.onerror = null;
        if (mediaSource && mediaSource.readyState === 'open') {
          try { sourceBuffer.abort(); } catch {}
          try { mediaSource.removeSourceBuffer(sourceBuffer); } catch {}
        }
      }
    } catch {}
    sourceBuffer = null;

    try {
      if (mediaSource) {
        mediaSource.onsourceopen = mediaSource.onsourceended = mediaSource.onsourceclose = null;
        if (mediaSource.readyState === 'open') {
          try { mediaSource.endOfStream(); } catch {}
        }
      }
    } catch {}
    mediaSource = null;
    mseCodec = null;
    if (mediaUrl) URL.revokeObjectURL(mediaUrl);
    mediaUrl = null;

    try {
      if (audioEl) {
        audioEl.pause();
        audioEl.removeAttribute('src');
        audioEl.load();
      }
    } catch {}
  }

  function startWorker(url) {
    if (worker) { try { worker.terminate(); } catch {} worker = null; }
    worker = new Worker("./js/audio/audio-ws-worker.js");
    const activeWorker = worker;
    worker.onmessage = (e) => {
      if (worker !== activeWorker) return;
      const { type } = e.data || {};
      if (type === 'open' || type === 'close') {
        if (type === 'close') {
          if (e.data.epoch !== transportEpoch) return;
          transportEpoch = null;
        } else {
          transportEpoch = e.data.epoch;
        }
        // A lost connection is not a user stop. Keep the same element, source
        // and media session so transient jitter does not release car audio focus.
        queue = []; queueBytes = 0; rebasePending = true;
        if (type === 'open') {
          if (!mediaSource || mseCodec !== codec || audioEl.error || mediaSource.readyState === 'closed') {
            destroyMSE(); appending = false; pendingAck = null; setupMSE();
          }
          refreshMediaMetadata();
        }
      } else if (type === 'data') {
        if (e.data.epoch !== transportEpoch) return;
        const ab = e.data.buf;
        if (ab && ab.byteLength) enqueue(new Uint8Array(ab), e.data.id, e.data.receivedAt, e.data.epoch);
        processQueue();
      } else if (type === 'quality' && [64000,128000,256000].includes(e.data.bitRate)) {
        aacBitRate=e.data.bitRate;announceCodec();
      } else if (type === 'error') {
        log('WS worker error:', e.data.message);
      }
    };
    worker.postMessage({ type: 'start', url, codec, bitRate:ceilingBitRate,currentBitRate:aacBitRate,automatic:automaticQuality });
  }

  function stopWorker() {
    if (!worker) return;
    try { worker.postMessage({ type: 'stop' }); worker.terminate(); } catch {}
    worker = null;
    transportEpoch = null;
  }

  function enqueue(u8, id, receivedAt, epoch) {
    const age = Date.now() - receivedAt;
    if (!Number.isSafeInteger(id) || id <= 0 || !Number.isFinite(age) || age < 0 || age > MAX_QUEUE_AGE_MS ||
        queue.length >= 16 || queueBytes + u8.byteLength > MAX_QUEUE_BYTES) {
      recoverPipelineSoon(false); return;
    }
    queue.push({ u8, id, receivedAt, epoch }); queueBytes += u8.byteLength;
  }

  function processQueue() {
    if (recovering || !sourceBuffer || sourceBuffer.updating || appending || !queue.length) return;
    const item = queue.shift(); queueBytes -= item.u8.byteLength;
    if (Date.now() - item.receivedAt > MAX_QUEUE_AGE_MS) { recoverPipelineSoon(false); return; }
    appending = true; pendingAck = { id: item.id, epoch: item.epoch, owner: worker };
    try {
      if (rebasePending) {
        const b = sourceBuffer.buffered;
        sourceBuffer.timestampOffset = Math.max(audioEl.currentTime, b.length ? b.end(b.length - 1) : 0);
        rebasePending = false;
      }
      sourceBuffer.appendBuffer(item.u8);
    }
    catch (_) { pendingAck = null; appending = false; recoverPipelineSoon(); }
    // Keep ownership through updateend, even after updating becomes false.
    // A queued worker message can otherwise overwrite the unacknowledged ID.
  }

  function getBufferedRemain() {
    if (!sourceBuffer || !audioEl) return 0;
    const b = sourceBuffer.buffered;
    if (!b || b.length === 0) return 0;
    const end = b.end(b.length - 1);
    return Math.max(0, end - audioEl.currentTime);
  }

  function maybeTrimOld() {
    if (!sourceBuffer || sourceBuffer.updating || appending || !audioEl) return;
    const b = sourceBuffer.buffered;
    if (!b || b.length === 0) return;

    const ct = audioEl.currentTime;
    if (ct <= 4.0) return;

    const start0 = b.start(0);
    const removeEnd = Math.max(ct - KEEP_TAIL_SEC, start0);
    if (removeEnd > start0 + 0.001) {
      try { appending = true; sourceBuffer.remove(start0, removeEnd); } catch { appending = false; }
    }
  }

  function maybeReduceLatency() {
    if (!sourceBuffer || sourceBuffer.updating || !audioEl || delayedStartPending) return;
    const remain = getBufferedRemain();
    const extraDelay = Math.max(0, audioDelayMs) / 1000;
    if (remain > MAX_LATENCY_SEC + extraDelay) {
      const b = sourceBuffer.buffered;
      if (!b || b.length === 0) return;
      const end = b.end(b.length - 1);
      const target = Math.max(b.start(b.length - 1), end - HARD_JUMP_SEC - extraDelay);
      try { audioEl.currentTime = target; } catch {}
    }
  }

  function maybeStartDelayedAudio() {
    if (!delayedStartPending || delayedStartPaused || delayedStartTimer !== null || !isAudioEnabled ||
        !sourceBuffer || sourceBuffer.updating || !sourceBuffer.buffered?.length || !(audioEl?.readyState >= 3)) return;
    const activeSource = mediaSource;
    // Delay from media readiness, not network connect time or a guessed VM offset.
    delayedStartTimer = setTimeout(() => {
      delayedStartTimer = null;
      if (!delayedStartPending || delayedStartPaused || !isAudioEnabled || mediaSource !== activeSource) return;
      delayedStartPending = false;
      maybeReduceLatency();
      audioEl.play().then(() => {
        if (started && mediaSource === activeSource && !audioEl.paused && 'mediaSession' in navigator) {
          navigator.mediaSession.playbackState = 'playing';
        }
      }).catch(()=>{});
    }, audioDelayMs);
  }

  function recoverPipelineSoon(rebuild = true) {
    if (recovering || !started || !isAudioEnabled) return;
    if (rebuild) aacFailure();
    recovering = true;
    if (rebuild) audioEl?.pause();
    stopWorker(); queue = []; queueBytes = 0; rebasePending = true;
    recoveryTimer = setTimeout(() => {
      recoveryTimer = null;
      if (!started || !isAudioEnabled) { recovering = false; return; }
      const preservePause = userPaused;
      if (rebuild) { stopAudio(true); startAudio(preservePause); }
      else startWorker(window.audioWebsocketUrl);
      recovering = false;
    }, 250);
  }

  function startAudio(preservePause = false) {
    if (!isAudioEnabled || !window.audioWebsocketUrl || !window.MediaSource) return false;
    if (codec === 'aac' && !MediaSource.isTypeSupported(MIME_TYPES.aac)) {
      codec = 'flac'; fallbackReason = 'This browser does not support AAC; using FLAC.'; announceCodec();
    }
    if (!MediaSource.isTypeSupported(MIME_TYPES[codec])) return false;

    userPaused = preservePause;
    delayedStartPending = audioDelayMs > 0;
    delayedStartPaused = preservePause;
    const el = ensureAudioEl();
    el.autoplay = !delayedStartPending && !userPaused;
    if (delayedStartPending || userPaused) el.pause();
    resumeAudioContextIfNeeded();
    setupMSE();
    startWorker(window.audioWebsocketUrl);

    setupMediaSession();

    if (!delayedStartPending && !userPaused) ensureAudioEl().play().catch(()=>{});
    return true;
  }

  function stopAudio(preserveSession = false) {
    clearTimeout(delayedStartTimer); delayedStartTimer = null; delayedStartPending = false; delayedStartPaused = false;
    if (trimmingTimer) { clearInterval(trimmingTimer); trimmingTimer = null; }
    stopWorker();
    destroyMSE();

    queue.length = 0;
    queueBytes = 0; pendingAck = null;
    appending = false;

    if (!preserveSession) teardownMediaSession();
  }

  let mediaRotateTimer = null;
  let artworkImage = null;
  let metadataRefreshTimers=[];
  function refreshMetadataBurst() {
    for(const timer of metadataRefreshTimers)clearTimeout(timer);
    metadataRefreshTimers=[250,1000,2500,5000].map(ms=>setTimeout(()=>{if(isAudioEnabled && !userPaused)refreshMediaMetadata();},ms));
  }
  function warmArtwork() {
    if(artworkImage || typeof Image!=='function')return;
    artworkImage=new Image();
    artworkImage.onload=()=>{if(started && isAudioEnabled){refreshMediaMetadata();refreshMetadataBurst();}};
    artworkImage.onerror=()=>{artworkImage=null;};
    artworkImage.src=new URL('./assets/droiddeck-logo.png?v=20261027-grey',window.location.href).href;
  }
  function refreshMediaMetadata() {
    if (!('mediaSession' in navigator)) return;
    try {
      const artwork = new URL('./assets/droiddeck-logo.png?v=20261027-grey', window.location.href).href;
      navigator.mediaSession.metadata = new MediaMetadata({ title: 'DroidMate Audio', artist: 'Live', album: 'DroidMate',
        artwork: [{ src: artwork, sizes: '1254x1254', type: 'image/png' }] });
      // Warm the same-origin image and reapply once decoded. Some car shells
      // ask for metadata before their artwork loader has finished its first fetch.
      warmArtwork();
    } catch (_) {}
  }
  function setupMediaSession() {
    if (!('mediaSession' in navigator)) return;
    try {
      refreshMediaMetadata();
      navigator.mediaSession.setActionHandler('play', () => {
        userPaused = false;
        refreshMediaMetadata();
        if (delayedStartPending) { delayedStartPaused = false; maybeStartDelayedAudio(); return; }
        audioEl?.play(); navigator.mediaSession.playbackState = 'playing';
      });
      navigator.mediaSession.setActionHandler('pause', () => {
        userPaused = true;
        if (delayedStartPending) {
          delayedStartPaused = true; clearTimeout(delayedStartTimer); delayedStartTimer = null;
        }
        audioEl?.pause(); navigator.mediaSession.playbackState = 'paused';
      });
      navigator.mediaSession.playbackState = delayedStartPaused ? 'paused' : 'playing';

      if (mediaRotateTimer) clearInterval(mediaRotateTimer);
      mediaRotateTimer = setInterval(refreshMediaMetadata, 60000);
    } catch(_) {}
  }

  function teardownMediaSession() {
    if (!('mediaSession' in navigator)) return;
    try {
      for(const timer of metadataRefreshTimers)clearTimeout(timer);metadataRefreshTimers=[];
      navigator.mediaSession.playbackState = 'none';
      if (mediaRotateTimer) { clearInterval(mediaRotateTimer); mediaRotateTimer = null; }
    } catch(_) {}
  }
  warmArtwork();
})();
