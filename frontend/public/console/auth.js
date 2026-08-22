/*
 * frontend/public/console/auth.js — Supabase Auth for the dashboard.
 *
 * Real sessions, not a mock: signInWithPassword / signInWithOtp / signUp
 * against Supabase, the JWT attached to every API call, and onAuthStateChange
 * kept live so a sign-out in another tab closes this one too.
 *
 * Set supabaseUrl + supabaseAnonKey in config.js and the dashboard gates on a
 * session. Leave them blank and it runs open and says so — a missing key is
 * never the thing that stops a demo, but it is also never silently treated as
 * "signed in".
 */
(function () {
  'use strict';

  var CFG = window.FORGE_CONFIG || {};
  var qs = new URLSearchParams(location.search);

  var client = null;
  var session = null;
  var enabled = false;
  var listeners = [];

  function configured() {
    return !!(CFG.supabaseUrl && CFG.supabaseAnonKey) && !qs.has('noauth');
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ─────────────────────────────────────────────────────────── sign-in view
  function renderSignIn(message, tone) {
    var host = document.getElementById('auth-screen');
    host.hidden = false;
    host.innerHTML =
      '<div class="w-full max-w-[430px]">' +

        '<a href="index.html" class="mb-9 flex items-center gap-2.5">' +
          '<svg width="21" height="21" viewBox="0 0 22 22" fill="none" aria-hidden="true">' +
            '<path d="M2.5 19.5V2.5h17L11.5 10.5H7v2.6h6.2L11 19.5H2.5z" fill="#fff"/></svg>' +
          '<span class="text-[16px] font-semibold tracking-[0.20em] text-ink">FORGE</span>' +
        '</a>' +

        '<div class="card-edge tickrow rounded-2xl p-8">' +
          '<h1 class="text-[26px] font-medium leading-tight tracking-[-0.02em]">Sign in' +
            '<span class="block silver">to the factory floor</span></h1>' +
          '<p class="mt-3 text-[15px] leading-relaxed text-dim">' +
            'This dashboard controls a factory that writes and ships code. Access is restricted.</p>' +

          '<form id="auth-form" class="mt-7 space-y-4" novalidate>' +
            '<div>' +
              '<label for="auth-email" class="lbl mb-2 block">Email</label>' +
              '<input id="auth-email" type="email" autocomplete="email" required ' +
                'class="w-full rounded-lg border border-white/[0.10] bg-white/[0.03] px-3.5 py-3 ' +
                'text-[15px] text-ink outline-none placeholder:text-mute focus:border-info">' +
            '</div>' +
            '<div>' +
              '<label for="auth-password" class="lbl mb-2 block">Password</label>' +
              '<input id="auth-password" type="password" autocomplete="current-password" ' +
                'class="w-full rounded-lg border border-white/[0.10] bg-white/[0.03] px-3.5 py-3 ' +
                'text-[15px] text-ink outline-none placeholder:text-mute focus:border-info">' +
            '</div>' +

            '<div id="auth-msg" class="min-h-[22px] text-[14px] ' +
              (tone === 'ok' ? 'text-ok' : 'text-bad') + '">' + esc(message || '') + '</div>' +

            '<button type="submit" id="auth-submit" ' +
              'class="w-full rounded-full bg-white py-3 text-[14px] font-semibold tracking-[0.04em] ' +
              'text-black hover:bg-white/90 disabled:opacity-50">SIGN IN</button>' +

            '<button type="button" id="auth-magic" ' +
              'class="pill-dark w-full rounded-full py-3 text-[13px] font-semibold tracking-[0.10em] text-ink">' +
              'EMAIL ME A LINK</button>' +
          '</form>' +

          '<div class="mt-6 border-t border-white/[0.07] pt-5 text-center text-[14px] text-dim">' +
            'No account? <button id="auth-signup" class="text-ink underline underline-offset-4">Create one</button>' +
          '</div>' +
        '</div>' +

        '<p class="mt-5 text-center text-[13px] text-mute">' +
          'Authentication is Supabase. <a href="index.html" class="underline underline-offset-4 hover:text-dim">' +
          'Back to the site</a></p>' +
      '</div>';

    var form = document.getElementById('auth-form');
    var emailEl = document.getElementById('auth-email');
    var passEl = document.getElementById('auth-password');
    var msgEl = document.getElementById('auth-msg');
    var submitEl = document.getElementById('auth-submit');

    function say(text, ok) {
      msgEl.textContent = text;
      msgEl.className = 'min-h-[22px] text-[14px] ' + (ok ? 'text-ok' : 'text-bad');
    }
    function busy(on, label) {
      submitEl.disabled = on;
      submitEl.textContent = on ? label : 'SIGN IN';
    }

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      if (!emailEl.value || !passEl.value) return say('Email and password are both required.');
      busy(true, 'SIGNING IN…');
      client.auth.signInWithPassword({ email: emailEl.value.trim(), password: passEl.value })
        .then(function (r) { busy(false); if (r.error) say(r.error.message); })
        .catch(function (err) { busy(false); say(String((err && err.message) || err)); });
    });

    document.getElementById('auth-magic').addEventListener('click', function () {
      if (!emailEl.value) return say('Enter your email first.');
      say('Sending…', true);
      client.auth.signInWithOtp({ email: emailEl.value.trim() })
        .then(function (r) {
          if (r.error) return say(r.error.message);
          say('Check your email for the sign-in link.', true);
        })
        .catch(function (err) { say(String((err && err.message) || err)); });
    });

    document.getElementById('auth-signup').addEventListener('click', function () {
      if (!emailEl.value || !passEl.value) return say('Email and password are both required.');
      say('Creating…', true);
      client.auth.signUp({ email: emailEl.value.trim(), password: passEl.value })
        .then(function (r) {
          if (r.error) return say(r.error.message);
          say(r.data && r.data.session ? 'Signed in.' : 'Account created — confirm your email, then sign in.', true);
        })
        .catch(function (err) { say(String((err && err.message) || err)); });
    });

    emailEl.focus();
  }

  function hideSignIn() {
    var host = document.getElementById('auth-screen');
    host.hidden = true;
    host.innerHTML = '';
  }

  // ──────────────────────────────────────────────────────────────────── api
  /**
   * Resolves once the dashboard may render: either auth is off, or a session
   * exists. Never rejects — a broken auth config degrades to open mode with a
   * visible warning rather than a blank screen.
   */
  function init() {
    return new Promise(function (resolve) {
      if (!configured()) {
        enabled = false;
        return resolve({ enabled: false, reason: qs.has('noauth') ? 'bypassed' : 'unconfigured' });
      }
      if (!window.supabase || !window.supabase.createClient) {
        enabled = false;
        console.warn('[forge] supabase-js did not load; dashboard is running open.');
        return resolve({ enabled: false, reason: 'sdk-unavailable' });
      }
      try {
        client = window.supabase.createClient(CFG.supabaseUrl, CFG.supabaseAnonKey);
      } catch (err) {
        enabled = false;
        console.warn('[forge] supabase client failed to build; running open.', err);
        return resolve({ enabled: false, reason: 'client-error' });
      }
      enabled = true;

      // Live: a sign-out in another tab closes this one, a magic-link return
      // lands straight in the dashboard without a reload.
      client.auth.onAuthStateChange(function (_event, s) {
        session = s;
        listeners.forEach(function (cb) { try { cb(s); } catch (e) { /* keep going */ } });
        if (s) { hideSignIn(); resolve({ enabled: true, session: s, user: s.user }); }
      });

      client.auth.getSession().then(function (r) {
        session = (r && r.data && r.data.session) || null;
        if (session) { hideSignIn(); resolve({ enabled: true, session: session, user: session.user }); }
        else renderSignIn('');
      }).catch(function () {
        renderSignIn('Could not reach Supabase. Check supabaseUrl in config.js.');
      });
    });
  }

  window.ForgeAuth = {
    init: init,
    enabled: function () { return enabled; },
    user: function () { return session && session.user ? session.user : null; },
    token: function () { return session && session.access_token ? session.access_token : null; },
    onChange: function (cb) { listeners.push(cb); },
    signOut: function () {
      if (!client) return Promise.resolve();
      return client.auth.signOut().then(function () {
        session = null;
        document.getElementById('app').hidden = true;
        renderSignIn('Signed out.', 'ok');
      });
    },
  };
})();
