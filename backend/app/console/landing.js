/*
 * forge/console/landing.js — the two generative backdrops and the page's
 * small interactions. No dependencies, no build step.
 *
 *   heroViz()   the hyperboloid. A hyperboloid of one sheet is a ruled
 *               surface: every point lies on a straight line joining the top
 *               rim to the bottom rim, twisted. Draw a few hundred of those
 *               straight lines and the curved waist appears on its own.
 *   rainViz()   the falling streaks behind the closing call to action.
 *
 * Both honour prefers-reduced-motion by painting one static frame.
 */
(function () {
  'use strict';

  var REDUCED = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function fit(canvas, ctx) {
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = Math.max(1, Math.round(w * dpr));
    canvas.height = Math.max(1, Math.round(h * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { w: w, h: h };
  }

  // ══════════════════════════════════════════════════════════ hero: the vortex

  function heroViz(canvas) {
    var ctx = canvas.getContext('2d');
    var size = fit(canvas, ctx);

    var RIM = 168;        // number of ruled lines around the rim
    var SPIRALS = 150;    // arcs making the flared base
    var STARS = 340;
    var TILT = 0.28;      // camera pitch, so the base reads as an ellipse
    var TWIST = 2.52;     // radians between a line's top and bottom anchor
    var R_TOP = 1.28, H_TOP = 1.26;   // upper rim: wide, and closer to the eye
    var R_BOT = 1.52, H_BOT = 1.72;   // lower rim: wider, and further down

    var stars = [];
    for (var i = 0; i < STARS; i++) {
      stars.push({
        x: Math.random(), y: Math.random(),
        a: Math.random() * 0.55 + 0.12,
        r: Math.random() * 1.0 + 0.25,
        tw: Math.random() * Math.PI * 2,
      });
    }

    var rot = 0;

    function project(x, y, z, scale, cx, cy) {
      // pitch about X, then a simple perspective divide
      var cy0 = Math.cos(TILT), sy0 = Math.sin(TILT);
      var y2 = y * cy0 - z * sy0;
      var z2 = y * sy0 + z * cy0;
      var d = 5.0;
      var s = d / (d - z2);
      return [cx + x * s * scale, cy + y2 * s * scale, s];
    }

    function frame(t) {
      var w = size.w, h = size.h;
      var cx = w / 2, cy = h * 0.46;
      var scale = Math.min(w, h) * 0.255;

      ctx.clearRect(0, 0, w, h);

      // --- starfield -------------------------------------------------------
      for (var s = 0; s < stars.length; s++) {
        var st = stars[s];
        var tw = REDUCED ? 1 : 0.65 + 0.35 * Math.sin(t * 0.0011 + st.tw);
        ctx.globalAlpha = st.a * tw;
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(st.x * w, st.y * h, st.r, st.r);
      }

      // --- the flared base: spiral arcs on the lower plane ------------------
      ctx.lineWidth = 0.55;
      ctx.strokeStyle = '#ffffff';
      for (var k = 0; k < SPIRALS; k++) {
        var a0 = (k / SPIRALS) * Math.PI * 2 + rot * 0.55;
        ctx.beginPath();
        for (var seg = 0; seg <= 30; seg++) {
          var f = seg / 30;
          var rad = R_BOT + f * 2.55;                // flare outward
          var ang = a0 + f * 1.35;                   // and sweep round
          var yy = H_BOT + f * 0.34;                 // drop away slightly
          var p = project(Math.cos(ang) * rad, yy, Math.sin(ang) * rad, scale, cx, cy);
          if (seg === 0) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]);
        }
        var facing = (Math.sin(a0 + 0.7) + 1) / 2;   // near side of the disc is brighter
        ctx.globalAlpha = 0.055 + facing * 0.115;
        ctx.stroke();
      }

      // --- the hyperboloid: straight lines, curved silhouette ---------------
      ctx.lineWidth = 0.7;
      ctx.strokeStyle = '#ffffff';
      for (var j = 0; j < RIM; j++) {
        var a = (j / RIM) * Math.PI * 2 + rot;
        var top = project(Math.cos(a) * R_TOP, -H_TOP, Math.sin(a) * R_TOP, scale, cx, cy);
        var bot = project(Math.cos(a + TWIST) * R_BOT, H_BOT, Math.sin(a + TWIST) * R_BOT, scale, cx, cy);

        // lines on the near side of the form read brighter
        var near = (Math.sin(a + TWIST * 0.5) + 1) / 2;
        ctx.globalAlpha = 0.085 + near * 0.30;
        ctx.beginPath();
        ctx.moveTo(top[0], top[1]);
        ctx.lineTo(bot[0], bot[1]);
        ctx.stroke();

        // and the mirrored family, which is what closes the waist
        var bot2 = project(Math.cos(a - TWIST) * R_BOT, H_BOT, Math.sin(a - TWIST) * R_BOT, scale, cx, cy);
        ctx.globalAlpha = 0.055 + near * 0.19;
        ctx.beginPath();
        ctx.moveTo(top[0], top[1]);
        ctx.lineTo(bot2[0], bot2[1]);
        ctx.stroke();
      }

      ctx.globalAlpha = 1;
    }

    function loop(t) {
      rot += 0.0011;
      frame(t);
      if (!REDUCED) requestAnimationFrame(loop);
    }

    window.addEventListener('resize', function () { size = fit(canvas, ctx); frame(performance.now()); });
    if (REDUCED) frame(0); else requestAnimationFrame(loop);
  }

  // ═══════════════════════════════════════════════════════════ closing: rain

  function rainViz(canvas) {
    var ctx = canvas.getContext('2d');
    var size = fit(canvas, ctx);
    var drops = [];

    function seed() {
      drops = [];
      var n = Math.round(size.w / 9);
      for (var i = 0; i < n; i++) {
        drops.push({
          x: Math.random() * size.w,
          y: Math.random() * size.h,
          len: 20 + Math.random() * 130,
          v: 0.35 + Math.random() * 1.5,
          a: 0.05 + Math.random() * 0.28,
        });
      }
    }
    seed();

    function frame() {
      ctx.clearRect(0, 0, size.w, size.h);
      ctx.lineWidth = 1;
      for (var i = 0; i < drops.length; i++) {
        var d = drops[i];
        var g = ctx.createLinearGradient(d.x, d.y, d.x, d.y + d.len);
        g.addColorStop(0, 'rgba(255,255,255,0)');
        g.addColorStop(1, 'rgba(255,255,255,' + d.a.toFixed(3) + ')');
        ctx.strokeStyle = g;
        ctx.beginPath();
        ctx.moveTo(d.x, d.y);
        ctx.lineTo(d.x, d.y + d.len);
        ctx.stroke();

        if (!REDUCED) {
          d.y += d.v;
          if (d.y > size.h) { d.y = -d.len - Math.random() * 200; d.x = Math.random() * size.w; }
        }
      }
      if (!REDUCED) requestAnimationFrame(frame);
    }

    window.addEventListener('resize', function () { size = fit(canvas, ctx); seed(); if (REDUCED) frame(); });
    frame();
  }

  // ══════════════════════════════════════════════════════════════ page bits

  function initReveal() {
    if (REDUCED || !('IntersectionObserver' in window)) {
      document.querySelectorAll('[data-reveal]').forEach(function (el) { el.classList.add('shown'); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add('shown'); io.unobserve(e.target); }
      });
    }, { rootMargin: '0px 0px -12% 0px', threshold: 0.06 });

    var watched = document.querySelectorAll('[data-reveal]');
    watched.forEach(function (el) { io.observe(el); });

    // Safety net. A reveal is a nicety; content that never un-hides is a broken
    // page. If the observer has not reported on something already in view by
    // now, show it anyway and stop watching it.
    setTimeout(function () {
      watched.forEach(function (el) {
        if (el.classList.contains('shown')) return;
        var r = el.getBoundingClientRect();
        if (r.top < window.innerHeight && r.bottom > 0) { el.classList.add('shown'); io.unobserve(el); }
      });
    }, 1200);
  }

  function initPricingToggles() {
    document.querySelectorAll('[data-billing]').forEach(function (toggle) {
      toggle.addEventListener('click', function () {
        var card = toggle.closest('[data-plan]');
        var yearly = toggle.getAttribute('aria-checked') !== 'true';
        toggle.setAttribute('aria-checked', String(yearly));
        toggle.querySelector('[data-knob]').style.transform = yearly ? 'translateX(18px)' : 'translateX(0)';
        toggle.classList.toggle('bg-white/80', yearly);
        toggle.classList.toggle('bg-white/15', !yearly);
        var price = card.querySelector('[data-price]');
        price.textContent = yearly ? price.dataset.yearly : price.dataset.monthly;
      });
    });
  }

  function initNav() {
    var nav = document.getElementById('nav');
    if (!nav) return;
    var onScroll = function () {
      var solid = window.scrollY > 24;
      nav.classList.toggle('bg-black/80', solid);
      nav.classList.toggle('backdrop-blur-xl', solid);
      nav.classList.toggle('border-white/[0.08]', solid);
      nav.classList.toggle('border-transparent', !solid);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  document.addEventListener('DOMContentLoaded', function () {
    var hero = document.getElementById('hero-canvas');
    if (hero) heroViz(hero);
    var rain = document.getElementById('rain-canvas');
    if (rain) rainViz(rain);
    initReveal();
    initPricingToggles();
    initNav();
    var year = document.getElementById('year');
    if (year) year.textContent = new Date().getFullYear();
  });
})();
