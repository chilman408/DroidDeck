// SPDX-License-Identifier: GPL-3.0-only
// Gateway-owned live audio player; protected frontend receipts remain unchanged.
(() => {
  const MIME_TYPES = { flac: 'audio/mp4; codecs="flac"', aac: 'audio/mp4; codecs="mp4a.40.2"' };
  const query = new URLSearchParams(window.location?.search || '');
  const requestedCodec = query.getAll('audio').length === 1 && query.get('audio') === 'flac' ? 'flac' : 'aac';
  let codec = requestedCodec, fallbackReason = null, aacFailures = 0, aacProven = false;
  const announceCodec = () => { try { window.dispatchEvent(new CustomEvent('droiddeck-audio-format')); } catch (_) {} };
  window.DroidDeckAudio = Object.freeze({
    snapshot: () => ({ requested: requestedCodec, effective: codec, targetBitRate: codec === 'aac' ? 128000 : null,
      fallbackReason, playing: Boolean(audioEl && !audioEl.paused && audioEl.currentTime > 0 && !audioEl.error) }),
    select(value) {
      if (!['flac', 'aac'].includes(value)) throw new RangeError('Invalid audio format');
      const url = new URL(window.location.href); url.searchParams.delete('audio');
      if (value === 'flac') url.searchParams.set('audio', 'flac');
      if (url.href === window.location.href) window.location.reload(); else window.location.replace(url.href);
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
    audioEl.addEventListener('canplay', maybeStartDelayedAudio);
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
            worker?.postMessage({ type: "ack", id: pendingAck }); pendingAck = null;
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
      if (type === 'close' && aacFailure()) { recoverPipelineSoon(); return; }
      if (type === 'open' || type === 'close') {
        // A new socket has a new init segment and a fresh live timeline.
        destroyMSE(); queue = []; queueBytes = 0; pendingAck = null; appending = false;
        if (trimmingTimer) { clearInterval(trimmingTimer); trimmingTimer = null; }
        clearTimeout(delayedStartTimer); delayedStartTimer = null;
        delayedStartPending = audioDelayMs > 0; delayedStartPaused = userPaused;
        if (type === 'open') { audioEl.autoplay = !delayedStartPending && !userPaused; setupMSE(); if (!delayedStartPending && !userPaused) audioEl.play().catch(()=>{}); }
      } else if (type === 'data') {
        const ab = e.data.buf;
        if (ab && ab.byteLength) enqueue(new Uint8Array(ab), e.data.id, e.data.receivedAt);
        processQueue();
      } else if (type === 'error') {
        log('WS worker error:', e.data.message);
      }
    };
    worker.postMessage({ type: 'start', url, codec });
  }

  function stopWorker() {
    if (!worker) return;
    try { worker.postMessage({ type: 'stop' }); worker.terminate(); } catch {}
    worker = null;
  }

  function enqueue(u8, id, receivedAt) {
    const age = Date.now() - receivedAt;
    if (!Number.isSafeInteger(id) || id <= 0 || !Number.isFinite(age) || age < 0 || age > MAX_QUEUE_AGE_MS ||
        queue.length >= 16 || queueBytes + u8.byteLength > MAX_QUEUE_BYTES) {
      recoverPipelineSoon(); return;
    }
    queue.push({ u8, id, receivedAt }); queueBytes += u8.byteLength;
  }

  function processQueue() {
    if (recovering || !sourceBuffer || sourceBuffer.updating || appending || !queue.length) return;
    const item = queue.shift(); queueBytes -= item.u8.byteLength;
    if (Date.now() - item.receivedAt > MAX_QUEUE_AGE_MS) { recoverPipelineSoon(); return; }
    appending = true; pendingAck = item.id;
    try { sourceBuffer.appendBuffer(item.u8); }
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

  function recoverPipelineSoon() {
    if (recovering || !started || !isAudioEnabled) return;
    aacFailure(); recovering = true;
    // Stop stale sound immediately while reconnect backoff is pending.
    audioEl?.pause(); stopWorker(); queue = []; queueBytes = 0; pendingAck = null;
    recoveryTimer = setTimeout(() => {
      recoveryTimer = null;
      if (!started || !isAudioEnabled) { recovering = false; return; }
      const preservePause = userPaused;
      stopAudio();
      startAudio(preservePause);
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

  function stopAudio() {
    clearTimeout(delayedStartTimer); delayedStartTimer = null; delayedStartPending = false; delayedStartPaused = false;
    if (trimmingTimer) { clearInterval(trimmingTimer); trimmingTimer = null; }
    stopWorker();
    destroyMSE();

    queue.length = 0;
    queueBytes = 0; pendingAck = null;
    appending = false;

    teardownMediaSession();
  }

  let mediaRotateTimer = null;
  function setupMediaSession() {
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: 'DroidDeck Audio',
        artist: 'Live',
        album: 'DroidDeck',
        artwork: [{ src: new URL('./assets/droiddeck-logo.png', window.location.href).href, sizes: '1254x1254', type: 'image/png' }]
      });
      navigator.mediaSession.setActionHandler('play', () => {
        userPaused = false;
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
      mediaRotateTimer = setInterval(() => {
        navigator.mediaSession.metadata = new MediaMetadata({
          title: 'DroidDeck Audio',
          artist: 'Live',
          album: 'DroidDeck',
        artwork: [{ src: new URL('./assets/droiddeck-logo.png', window.location.href).href, sizes: '1254x1254', type: 'image/png' }]
        });
      }, 60000);
    } catch(_) {}
  }

  function teardownMediaSession() {
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.playbackState = 'none';
      if (mediaRotateTimer) { clearInterval(mediaRotateTimer); mediaRotateTimer = null; }
    } catch(_) {}
  }
})();
