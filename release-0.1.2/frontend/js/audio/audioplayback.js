(() => {
  const MIME = 'audio/mp4; codecs="flac"';

  const MAX_QUEUE_BYTES     = 2 * 1024 * 1024;
  const DROP_ON_BYTES       = 1.6 * 1024 * 1024;
  const RESUME_ON_BYTES     = 0.8 * 1024 * 1024;

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
  let dropRequested = false;

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
      ensureAudioEl();
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
        sourceBuffer = mediaSource.addSourceBuffer(MIME);
        sourceBuffer.mode = 'sequence';
        const activeBuffer = sourceBuffer;

        sourceBuffer.addEventListener('updateend', () => {
          if (mediaSource !== activeSource || sourceBuffer !== activeBuffer) return;
          processQueue();
          maybeStartDelayedAudio();
          maybeReduceLatency();
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
      if (type === 'data') {
        const ab = e.data.buf;
        if (ab && ab.byteLength) enqueue(new Uint8Array(ab));
        processQueue();
      } else if (type === 'error') {
        log('WS worker error:', e.data.message);
      }
    };
    worker.postMessage({ type: 'start', url });
  }

  function stopWorker() {
    if (!worker) return;
    try { worker.postMessage({ type: 'stop' }); worker.terminate(); } catch {}
    worker = null;
  }

  function setWorkerDropMode(drop) {
    if (!worker) return;
    if (drop === dropRequested) return;
    dropRequested = drop;
    try { worker.postMessage({ type: 'drop', drop }); } catch {}
  }

  function enqueue(u8) {
    while (queueBytes + u8.byteLength > MAX_QUEUE_BYTES && queue.length) {
      const old = queue.shift();
      queueBytes -= old.byteLength;
    }
    if (u8.byteLength > MAX_QUEUE_BYTES) return;

    queue.push(u8);
    queueBytes += u8.byteLength;

    if (queueBytes >= DROP_ON_BYTES) setWorkerDropMode(true);
    else if (queueBytes <= RESUME_ON_BYTES) setWorkerDropMode(false);
  }

  function processQueue() {
    if (!sourceBuffer || sourceBuffer.updating || appending) return;
    if (queue.length === 0) return;
    appending = true;

    const seg = queue.shift();
    queueBytes -= seg.byteLength;

    try {
      sourceBuffer.appendBuffer(seg);
    } catch (e) {
      queue.unshift(seg);
      queueBytes += seg.byteLength;
      appending = false;
      log('appendBuffer failed:', e);
      recoverPipelineSoon();
      return;
    }
    appending = false;

    if (queueBytes <= RESUME_ON_BYTES) setWorkerDropMode(false);
  }

  function getBufferedRemain() {
    if (!sourceBuffer || !audioEl) return 0;
    const b = sourceBuffer.buffered;
    if (!b || b.length === 0) return 0;
    const end = b.end(b.length - 1);
    return Math.max(0, end - audioEl.currentTime);
  }

  function maybeTrimOld() {
    if (!sourceBuffer || sourceBuffer.updating || !audioEl) return;
    const b = sourceBuffer.buffered;
    if (!b || b.length === 0) return;

    const ct = audioEl.currentTime;
    if (ct <= 4.0) return;

    const start0 = b.start(0);
    const removeEnd = Math.max(ct - KEEP_TAIL_SEC, start0);
    if (removeEnd > start0 + 0.001) {
      try { sourceBuffer.remove(start0, removeEnd); } catch {}
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
    if (recovering) return;
    recovering = true;
    recoveryTimer = setTimeout(() => {
      recoveryTimer = null;
      if (!started || !isAudioEnabled) { recovering = false; return; }
      const preservePause = delayedStartPaused;
      stopAudio();
      startAudio(preservePause);
      recovering = false;
    }, 800);
  }

  function startAudio(preservePause = false) {
    if (!isAudioEnabled || !window.audioWebsocketUrl || !window.MediaSource || !MediaSource.isTypeSupported(MIME)) return false;

    delayedStartPending = audioDelayMs > 0;
    delayedStartPaused = delayedStartPending && preservePause;
    const el = ensureAudioEl();
    el.autoplay = !delayedStartPending;
    if (delayedStartPending) el.pause();
    resumeAudioContextIfNeeded();
    setupMSE();
    startWorker(window.audioWebsocketUrl);

    setupMediaSession();

    if (!delayedStartPending) ensureAudioEl().play().catch(()=>{});
    return true;
  }

  function stopAudio() {
    clearTimeout(delayedStartTimer); delayedStartTimer = null; delayedStartPending = false; delayedStartPaused = false;
    if (trimmingTimer) { clearInterval(trimmingTimer); trimmingTimer = null; }
    stopWorker();
    destroyMSE();

    queue.length = 0;
    queueBytes = 0;
    setWorkerDropMode(false);
    appending = false;

    teardownMediaSession();
  }

  let mediaRotateTimer = null;
  function setupMediaSession() {
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: 'Lossless Audio - DroidMate',
        artist: 'Live',
        album: 'DroidMate'
      });
      navigator.mediaSession.setActionHandler('play', () => {
        if (delayedStartPending) { delayedStartPaused = false; maybeStartDelayedAudio(); return; }
        audioEl?.play(); navigator.mediaSession.playbackState = 'playing';
      });
      navigator.mediaSession.setActionHandler('pause', () => {
        if (delayedStartPending) {
          delayedStartPaused = true; clearTimeout(delayedStartTimer); delayedStartTimer = null;
        }
        audioEl?.pause(); navigator.mediaSession.playbackState = 'paused';
      });
      navigator.mediaSession.playbackState = delayedStartPaused ? 'paused' : 'playing';

      if (mediaRotateTimer) clearInterval(mediaRotateTimer);
      mediaRotateTimer = setInterval(() => {
        navigator.mediaSession.metadata = new MediaMetadata({
          title: 'Lossless Audio | DroidMate',
          artist: 'Live',
          album: 'DroidMate'
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
