/*
 * forge/console/chat-input.js — the brief composer.
 *
 * A vanilla port of the ai-chat-input reference component: the spring expand,
 * the auto-growing textarea with scroll fades, the morphing action button, and
 * dictation with a live level meter.
 *
 * Two controls from the reference are deliberately not here. The model picker
 * would be a lie — triage picks its own model server-side — and attachments
 * would be a lie too, because POST /api/brief takes text. The effort pill did
 * survive, remapped onto something real: the brief's priority, which is a field
 * the API actually reads. A control that does nothing is worse than no control.
 *
 * Dictation degrades honestly. If the browser has no SpeechRecognition the mic
 * is not offered; it never simulates words the user did not say.
 */
(function () {
  'use strict';

  var PRIORITIES = ['low', 'normal', 'high'];

  function svgBars(level) {
    var i = PRIORITIES.indexOf(level);
    return '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">' +
      '<rect x="1.5" y="8" width="2.5" height="4.5" rx="1" fill="currentColor" class="bar-lvl"/>' +
      '<rect x="5.75" y="5" width="2.5" height="7.5" rx="1" fill="currentColor" class="bar-lvl" opacity="' + (i >= 1 ? 1 : 0.3) + '"/>' +
      '<rect x="10" y="2" width="2.5" height="10.5" rx="1" fill="currentColor" class="bar-lvl" opacity="' + (i >= 2 ? 1 : 0.3) + '"/>' +
    '</svg>';
  }

  var ICON_SEND = '<svg width="13" height="13" viewBox="0 0 14 14" fill="none" aria-hidden="true">' +
    '<path d="M7 12V2M7 2L2.5 6.5M7 2L11.5 6.5" stroke="currentColor" stroke-width="1.75" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';

  var ICON_MIC = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">' +
    '<rect x="5" y="1" width="4" height="7" rx="2" stroke="currentColor" stroke-width="1.5"/>' +
    '<path d="M2.75 6.5V7a4.25 4.25 0 0 0 8.5 0v-.5M7 11.25V13" stroke="currentColor" ' +
    'stroke-width="1.5" stroke-linecap="round"/></svg>';

  var ICON_STOP = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">' +
    '<rect x="3.5" y="3.5" width="7" height="7" rx="1" fill="currentColor"/></svg>';

  var SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition || null;

  function mount(host, opts) {
    opts = opts || {};
    var placeholder = opts.placeholder || 'Describe the feature to build…';
    var minH = 96, maxH = 220;

    var priority = opts.priority || 'normal';
    var expanded = false;
    var recording = false;
    var busy = false;
    var recognition = null;
    var audioCtx = null, stream = null, raf = null;

    host.innerHTML =
      '<div data-card class="relative w-full border border-line bg-surface spring" ' +
           'style="height:56px;overflow:hidden">' +

        '<textarea data-ta rows="1" placeholder="' + placeholder.replace(/"/g, '&quot;') + '" ' +
          'class="absolute inset-x-0 top-0 z-[1] w-full resize-none bg-transparent px-4 py-3.5 ' +
          'text-[16px] leading-[24px] text-ink outline-none opacity-0 pointer-events-none ' +
          'overflow-y-hidden" style="height:' + minH + 'px"></textarea>' +

        // scroll fades, so long briefs read as scrollable rather than clipped
        '<div data-fade-top class="absolute left-4 right-12 top-0 z-[2] h-7 pointer-events-none" ' +
          'style="opacity:0;background:linear-gradient(to bottom,#111827,rgba(17,24,39,0))"></div>' +
        '<div data-fade-bot class="absolute left-4 right-12 z-[2] h-7 pointer-events-none" ' +
          'style="opacity:0;background:linear-gradient(to top,#111827,rgba(17,24,39,0))"></div>' +

        // collapsed affordance
        '<button type="button" data-open ' +
          'class="absolute inset-x-0 top-0 z-[1] w-full cursor-text px-4 py-[17px] text-left ' +
          'text-[16px] text-mute">' + placeholder + '</button>' +

        // bottom control row
        '<div data-actions class="absolute bottom-2 left-2 right-12 z-10 flex items-center ' +
          'opacity-0 pointer-events-none morph">' +
          '<button type="button" data-priority ' +
            'class="flex items-center gap-1.5 px-2 py-1 text-dim hover:text-ink hover:bg-raise">' +
            '<span data-bars>' + svgBars(priority) + '</span>' +
            '<span class="lbl text-[12px]" data-priority-label>' + priority + ' priority</span>' +
          '</button>' +
          '<span data-hint class="ml-auto mr-2 text-[13px] text-mute font-mono">↵ submit</span>' +
        '</div>' +

        // dictation level meter
        '<div data-meter class="absolute right-12 bottom-2 z-10 flex h-8 items-center justify-end ' +
          'gap-[3px] opacity-0 pointer-events-none morph"></div>' +

        '<button type="button" data-action aria-label="Submit brief" ' +
          'class="absolute right-2 bottom-2 z-10 flex h-8 w-8 items-center justify-center ' +
          'rounded-full bg-ink text-bg hover:bg-white disabled:opacity-40">' +
          '<span class="relative flex h-full w-full items-center justify-center">' +
            '<span data-i-send class="absolute inset-0 flex items-center justify-center morph">' + ICON_SEND + '</span>' +
            '<span data-i-mic class="absolute inset-0 flex items-center justify-center morph">' + ICON_MIC + '</span>' +
            '<span data-i-stop class="absolute inset-0 flex items-center justify-center morph">' + ICON_STOP + '</span>' +
          '</span>' +
        '</button>' +
      '</div>';

    var card = host.querySelector('[data-card]');
    var ta = host.querySelector('[data-ta]');
    var openBtn = host.querySelector('[data-open]');
    var actions = host.querySelector('[data-actions]');
    var actionBtn = host.querySelector('[data-action]');
    var meter = host.querySelector('[data-meter]');
    var fadeTop = host.querySelector('[data-fade-top]');
    var fadeBot = host.querySelector('[data-fade-bot]');
    var prioBtn = host.querySelector('[data-priority]');
    var prioLabel = host.querySelector('[data-priority-label]');
    var prioBars = host.querySelector('[data-bars]');
    var iSend = host.querySelector('[data-i-send]');
    var iMic = host.querySelector('[data-i-mic]');
    var iStop = host.querySelector('[data-i-stop]');

    for (var b = 0; b < 5; b++) {
      var bar = document.createElement('div');
      bar.className = 'w-1 bg-info';
      bar.style.height = '4px';
      bar.style.transition = 'height .08s ease-out';
      meter.appendChild(bar);
    }
    var bars = meter.querySelectorAll('div');

    // ------------------------------------------------------------- geometry
    function autoGrow() {
      var prev = ta.style.height;
      ta.style.transition = 'none';
      ta.style.height = '0px';
      var sh = ta.scrollHeight;
      ta.style.height = prev;
      void ta.offsetHeight;
      ta.style.transition = '';
      var h = Math.max(minH, Math.min(sh, maxH));
      ta.style.height = h + 'px';
      ta.classList.toggle('overflow-y-auto', sh > maxH);
      ta.classList.toggle('overflow-y-hidden', sh <= maxH);
      if (expanded) card.style.height = (h + 44) + 'px';
      fadeBot.style.top = (h - 28) + 'px';
      updateFades();
    }

    function updateFades() {
      fadeTop.style.opacity = String(Math.min(ta.scrollTop / 20, 1));
      var below = ta.scrollHeight - ta.clientHeight - ta.scrollTop;
      fadeBot.style.opacity = String(Math.min(Math.max(below - 12, 0) / 10, 1));
    }

    function show(el, on) {
      el.style.opacity = on ? '1' : '0';
      el.style.transform = on ? 'scale(1) rotate(0deg)' : 'scale(.5) rotate(45deg)';
      el.style.pointerEvents = 'none';
    }

    function syncAction() {
      var hasText = ta.value.trim() !== '';
      show(iSend, hasText && !recording);
      show(iMic, !hasText && !recording);
      show(iStop, recording);
      actionBtn.setAttribute('aria-label',
        recording ? 'Stop dictation' : hasText ? 'Submit brief' : 'Dictate brief');
      actionBtn.disabled = busy || (!hasText && !recording && !SpeechRec);
    }

    function setExpanded(on, fast) {
      if (expanded === on) return;
      expanded = on;
      card.className = card.className.replace(/spring(-fast)?/, fast ? 'spring-fast' : 'spring');
      card.style.overflow = on ? 'visible' : 'hidden';
      card.style.height = on ? (parseInt(ta.style.height, 10) + 44) + 'px' : '56px';
      ta.style.opacity = on ? '1' : '0';
      ta.style.pointerEvents = on ? 'auto' : 'none';
      openBtn.style.opacity = on ? '0' : '1';
      openBtn.style.pointerEvents = on ? 'none' : 'auto';
      actions.style.opacity = on && !recording ? '1' : '0';
      actions.style.pointerEvents = on && !recording ? 'auto' : 'none';
      card.classList.toggle('border-info', on);
    }

    // ------------------------------------------------------------ dictation
    function startDictation() {
      if (!SpeechRec) return;
      recording = true;
      setExpanded(true);
      actions.style.opacity = '0';
      actions.style.pointerEvents = 'none';
      meter.style.opacity = '1';
      syncAction();

      var baseline = ta.value;
      recognition = new SpeechRec();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.onresult = function (e) {
        var finalText = '', interim = '';
        for (var i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i].isFinal) finalText += e.results[i][0].transcript;
          else interim += e.results[i][0].transcript;
        }
        if (finalText) baseline += (baseline ? ' ' : '') + finalText.trim();
        ta.value = (baseline + (interim ? ' ' + interim : '')).trim();
        autoGrow();
        ta.scrollTop = ta.scrollHeight;
        syncAction();
        if (opts.onChange) opts.onChange(ta.value);
      };
      recognition.onerror = stopDictation;
      recognition.onend = stopDictation;
      try { recognition.start(); } catch (e) { stopDictation(); return; }

      if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
        navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
          if (!recording) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
          stream = s;
          var Ctx = window.AudioContext || window.webkitAudioContext;
          audioCtx = new Ctx();
          var analyser = audioCtx.createAnalyser();
          analyser.fftSize = 64;
          audioCtx.createMediaStreamSource(s).connect(analyser);
          var data = new Uint8Array(analyser.frequencyBinCount);
          var step = Math.floor(data.length / 5);
          (function tick() {
            analyser.getByteFrequencyData(data);
            for (var i = 0; i < 5; i++) {
              var sum = 0;
              for (var j = 0; j < step; j++) sum += data[i * step + j];
              bars[i].style.height = Math.max(4, (sum / step / 255) * 24) + 'px';
            }
            raf = requestAnimationFrame(tick);
          })();
        }).catch(function () { /* meter stays flat; dictation still works */ });
      }
    }

    function stopDictation() {
      if (recognition) { try { recognition.onend = null; recognition.stop(); } catch (e) {} recognition = null; }
      if (raf) { cancelAnimationFrame(raf); raf = null; }
      if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
      if (audioCtx) { try { audioCtx.close(); } catch (e) {} audioCtx = null; }
      recording = false;
      meter.style.opacity = '0';
      for (var i = 0; i < bars.length; i++) bars[i].style.height = '4px';
      if (expanded) { actions.style.opacity = '1'; actions.style.pointerEvents = 'auto'; }
      syncAction();
    }

    // --------------------------------------------------------------- events
    function submit() {
      var text = ta.value.trim();
      if (!text || busy) return;
      if (opts.onSubmit) opts.onSubmit(text, { priority: priority });
    }

    openBtn.addEventListener('click', function () { setExpanded(true); ta.focus(); });

    ta.addEventListener('input', function () {
      autoGrow();
      syncAction();
      if (opts.onChange) opts.onChange(ta.value);
    });
    ta.addEventListener('scroll', updateFades);
    ta.addEventListener('focus', function () { setExpanded(true); });
    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
      if (e.key === 'Escape' && !ta.value.trim()) { ta.blur(); setExpanded(false); }
      e.stopPropagation(); // number keys are screen shortcuts everywhere but here
    });
    ta.addEventListener('blur', function () {
      if (!ta.value.trim() && !recording) setExpanded(false);
    });

    prioBtn.addEventListener('click', function () {
      priority = PRIORITIES[(PRIORITIES.indexOf(priority) + 1) % PRIORITIES.length];
      prioBars.innerHTML = svgBars(priority);
      prioLabel.textContent = priority + ' priority';
      if (opts.onPriorityChange) opts.onPriorityChange(priority);
    });

    actionBtn.addEventListener('click', function () {
      if (recording) stopDictation();
      else if (ta.value.trim()) submit();
      else startDictation();
    });

    autoGrow();
    syncAction();

    // The Tailwind Play CDN applies its stylesheet asynchronously, so a first
    // measurement can land against an unstyled textarea and size the box wrong.
    // Measure again once layout has settled, and again on load.
    requestAnimationFrame(autoGrow);
    window.addEventListener('load', autoGrow);

    return {
      value: function () { return ta.value.trim(); },
      priority: function () { return priority; },
      focus: function () { setExpanded(true); ta.focus(); },
      setValue: function (v) {
        ta.value = v;
        setExpanded(true, true);
        autoGrow();
        syncAction();
        ta.focus();
        ta.setSelectionRange(v.length, v.length);
        if (opts.onChange) opts.onChange(v);
      },
      clear: function () {
        ta.value = '';
        autoGrow();
        syncAction();
        setExpanded(false);
      },
      setBusy: function (on, label) {
        busy = on;
        ta.disabled = on;
        actionBtn.disabled = on;
        host.querySelector('[data-hint]').textContent = on ? (label || 'submitting…') : '↵ submit';
      },
      destroy: function () { stopDictation(); host.innerHTML = ''; },
    };
  }

  window.ForgeChatInput = { mount: mount, dictationAvailable: !!SpeechRec };
})();
