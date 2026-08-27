/*
 * Companion Live2D lip-sync bridge.
 *
 * Inspired by easy-live2d's EffectController.playVoice ordering:
 * decode lip-sync PCM first, then begin audio playback. This compatibility
 * layer keeps the upstream frontend submodule untouched.
 */
(() => {
  "use strict";

  const originalPlay = HTMLMediaElement.prototype.play;
  const patchedHandlers = new WeakSet();
  const beforePlayHooks = new Set();
  const config = {
    gain: 1.35,
    silenceThreshold: 0.018,
    attack: 0.68,
    release: 0.34,
  };
  const status = {
    enabled: true,
    prepared: 0,
    fallbacks: 0,
    lastPrepareMs: 0,
    lastError: "",
  };

  function currentHandler() {
    try {
      return window.getLAppAdapter?.().getModel()?._wavFileHandler || null;
    } catch (_) {
      return null;
    }
  }

  function isCompanionSpeech(media) {
    const source = String(media.currentSrc || media.src || "");
    return source.startsWith("data:audio/wav;base64,");
  }

  function patchHandler(handler) {
    if (patchedHandlers.has(handler)) return;
    patchedHandlers.add(handler);

    const originalStart = handler.start?.bind(handler);
    if (originalStart) {
      handler.start = function startPreparedLipSync(source) {
        const prepared = this.__companionPrepared;
        if (prepared?.source === source) return prepared.promise;
        return originalStart(source);
      };
    }

    const originalGetRms = handler.getRms?.bind(handler);
    if (originalGetRms) {
      let smoothed = 0;
      handler.getRms = function getSmoothedRms() {
        let target = Number(originalGetRms()) || 0;
        target = target < config.silenceThreshold ? 0 : Math.min(1, target * config.gain);
        const rate = target > smoothed ? config.attack : config.release;
        smoothed += (target - smoothed) * rate;
        if (smoothed < config.silenceThreshold * 0.5) smoothed = 0;
        return smoothed;
      };
    }
  }

  async function prepareCompanionPlayback(media) {
    for (const hook of beforePlayHooks) {
      try {
        await hook(media);
      } catch (error) {
        console.warn("[Companion Audio] before-play hook failed", error);
      }
    }
    window.dispatchEvent(new CustomEvent("companion-speech-start", { detail: { media } }));
    media.addEventListener(
      "ended",
      () => window.dispatchEvent(new CustomEvent("companion-speech-end")),
      { once: true },
    );
  }

  function playAfterCompanionPreparation(media) {
    return prepareCompanionPlayback(media).then(() => originalPlay.call(media));
  }

  HTMLMediaElement.prototype.play = function companionSynchronizedPlay() {
    if (!isCompanionSpeech(this)) {
      return originalPlay.call(this);
    }

    const useLive2DLipSync =
      status.enabled && document.documentElement.dataset.companionRenderer !== "vrm";
    if (!useLive2DLipSync) return playAfterCompanionPreparation(this);

    const handler = currentHandler();
    if (!handler?.loadWavFile) {
      status.fallbacks += 1;
      return playAfterCompanionPreparation(this);
    }

    patchHandler(handler);
    const source = String(this.currentSrc || this.src);
    const started = performance.now();
    handler._sampleOffset = 0;
    handler._userTimeSeconds = 0;
    handler._lastRms = 0;

    const preload = Promise.resolve(handler.loadWavFile(source));
    handler.__companionPrepared = { source, promise: preload };

    return preload
      .then(async (ok) => {
        if (ok === false) throw new Error("Live2D PCM decode returned false");
        handler._sampleOffset = 0;
        handler._userTimeSeconds = 0;
        handler._lastRms = 0;
        status.prepared += 1;
        status.lastPrepareMs = Math.round((performance.now() - started) * 10) / 10;
        status.lastError = "";
        this.addEventListener(
          "ended",
          () => {
            if (handler.__companionPrepared?.source === source) {
              delete handler.__companionPrepared;
            }
          },
          { once: true },
        );
        await prepareCompanionPlayback(this);
        return originalPlay.call(this);
      })
      .catch((error) => {
        status.fallbacks += 1;
        status.lastError = String(error?.message || error);
        console.warn("[Companion LipSync] PCM preload failed; using normal playback", error);
        return playAfterCompanionPreparation(this);
      });
  };

  window.CompanionAudioBridge = {
    registerBeforePlay(hook) {
      if (typeof hook === "function") beforePlayHooks.add(hook);
      return () => beforePlayHooks.delete(hook);
    },
  };

  window.CompanionLipSync = {
    config,
    status,
    enable() {
      status.enabled = true;
    },
    disable() {
      status.enabled = false;
    },
    configure(options = {}) {
      for (const key of Object.keys(config)) {
        if (Number.isFinite(Number(options[key]))) config[key] = Number(options[key]);
      }
      return { ...config };
    },
  };
  document.documentElement.dataset.companionLipSync = "ready";
  fetch("/api/companion/lipsync")
    .then((response) => (response.ok ? response.json() : null))
    .then((settings) => {
      if (!settings) return;
      status.enabled = settings.enabled !== false;
      window.CompanionLipSync.configure({
        gain: settings.gain,
        silenceThreshold: settings.silence_threshold,
        attack: settings.attack,
        release: settings.release,
      });
    })
    .catch(() => {});
})();
