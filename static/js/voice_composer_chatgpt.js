/**
 * Shared helpers for ChatGPT-style dictation + read-aloud (teacher/student dashboards).
 * Depends on: fetch, AbortController, Audio, URL.
 */
(function (global) {
  'use strict';

  function stripAssistantPlain(html) {
    if (html == null) return '';
    var d = global.document.createElement('div');
    d.innerHTML = String(html);
    return String(d.textContent || d.innerText || '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  /**
   * Read-aloud with abortable fetch, single Audio instance, no overlap.
   * @param {object} opts
   * @param {() => string} opts.getSpeechLanguage
   * @param {(err: *) => void} [opts.logError]
   */
  function createReadAloudApi(opts) {
    opts = opts || {};
    var ttsAudio = null;
    var ttsObjectUrl = null;
    var abortCtl = null;
    var endPlayback = null;
    var ttsLoading = false;

    function stop() {
      ttsLoading = false;
      if (endPlayback) {
        try { endPlayback(); } catch (e) {}
        endPlayback = null;
      }
      if (abortCtl) {
        try { abortCtl.abort(); } catch (e) {}
        abortCtl = null;
      }
      if (ttsAudio) {
        try {
          ttsAudio.pause();
          ttsAudio.currentTime = 0;
        } catch (e) {}
        ttsAudio = null;
      }
      if (ttsObjectUrl) {
        try { global.URL.revokeObjectURL(ttsObjectUrl); } catch (e) {}
        ttsObjectUrl = null;
      }
    }

    function isPlaying() {
      return !!(ttsAudio && !ttsAudio.paused && !ttsAudio.ended);
    }

    function isLoading() {
      return !!ttsLoading;
    }

    /**
     * @param {string} plainText already plain (tags stripped ok)
     * @param {{ onFetchStart?: () => void, onPlaybackStart?: () => void, onPlaybackEnd?: () => void }} [hooks]
     */
    function playPlain(plainText, hooks) {
      hooks = hooks || {};
      stop();
      var plain = String(plainText || '').replace(/<[^>]*>/g, '').trim();
      if (!plain) {
        if (typeof hooks.onPlaybackEnd === 'function') hooks.onPlaybackEnd();
        return Promise.resolve();
      }

      ttsLoading = true;
      if (typeof hooks.onFetchStart === 'function') hooks.onFetchStart();

      abortCtl = typeof AbortController !== 'undefined' ? new AbortController() : null;
      var signal = abortCtl ? abortCtl.signal : undefined;

      return global
        .fetch('/text-to-speech', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          signal: signal,
          body: JSON.stringify({
            text: plain,
            language: typeof opts.getSpeechLanguage === 'function' ? opts.getSpeechLanguage() : 'en',
          }),
        })
        .then(function (res) {
          if (!res.ok) throw new Error('TTS HTTP ' + res.status);
          return res.blob();
        })
        .then(function (blob) {
          if (!blob || (signal && signal.aborted)) return;
          if (ttsObjectUrl) {
            try { global.URL.revokeObjectURL(ttsObjectUrl); } catch (e) {}
            ttsObjectUrl = null;
          }
          ttsObjectUrl = global.URL.createObjectURL(blob);
          ttsAudio = new global.Audio(ttsObjectUrl);
          return new Promise(function (resolve) {
            var playbackNotified = false;
            function notifyPlaybackStart() {
              if (playbackNotified) return;
              playbackNotified = true;
              ttsLoading = false;
              if (typeof hooks.onPlaybackStart === 'function') hooks.onPlaybackStart();
            }
            endPlayback = function () {
              endPlayback = null;
              resolve();
            };
            ttsAudio.addEventListener('playing', notifyPlaybackStart, { once: true });
            ttsAudio.onended = function () {
              if (endPlayback) endPlayback();
            };
            ttsAudio.onerror = function () {
              if (endPlayback) endPlayback();
            };
            ttsAudio.play().then(notifyPlaybackStart).catch(function () {
              if (endPlayback) endPlayback();
            });
          });
        })
        .catch(function (err) {
          ttsLoading = false;
          if (err && err.name === 'AbortError') return;
          if (typeof opts.logError === 'function') opts.logError(err);
        })
        .finally(function () {
          ttsLoading = false;
          if (typeof hooks.onPlaybackEnd === 'function') hooks.onPlaybackEnd();
          stop();
        });
    }

    return {
      stripAssistantPlain: stripAssistantPlain,
      stop: stop,
      isPlaying: isPlaying,
      isLoading: isLoading,
      playPlain: playPlain,
    };
  }

  global.IqbalVoiceComposer = {
    stripAssistantPlain: stripAssistantPlain,
    createReadAloudApi: createReadAloudApi,
  };
})(typeof window !== 'undefined' ? window : this);
