/*
 * forge/console/auth.js — Supabase Auth, optional.
 *
 * Fill supabaseUrl + supabaseAnonKey in config.js and the console gates on a
 * session and sends the access token on every API call. Leave them blank and
 * the console runs open and says so in the rail — a missing key is never what
 * stops a demo, but it is also never silently treated as "signed in".
 *
 * Buildless on purpose: the UMD bundle on window.supabase, no import step.
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

  // ---------------------------------------------------------------- sign-in
  function renderSignIn(message, tone) {
    var host = document.getElementById('auth-screen');
    host.hidden = false;
    host.innerHTML =
      '<div class="w-full max-w-[420px]">' +
        '<div class="flex items-center gap-2.5 mb-8">' +
          '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">' +
            '<path d="M2 16V2h14L9.5 8.5H6v3h5L9 16H2z" fill="#FB923C"/></svg>' +
          '<span class="text-[17px] font-bold tracking-[0.18em] text-ink">FORGE</span>' +
          '<span class="lbl text-[11px] ml-1">console</span>' +
        '</div>' +

        '<div class="frame border border-line bg-surface p-7">' +
          '<h1 class="text-[20px] font-semibold text-ink">Sign in</h1>' +
          '<p class="mt-1.5 text-[15px] text-dim">This console controls a factory that writes and ships code. Access is restricted.</p>' +

          '<form id="auth-form" class="mt-6 space-y-4" novalidate>' +
            '<div>' +
              '<label for="auth-email" class="lbl block mb-1.5">Email</label>' +
              '<input id="auth-email" type="email" autocomplete="email" required ' +
                'class="w-full bg-bg border border-line px-3 py-2.5 text-[15px] text-ink font-mono ' +
                'focus:border-info focus:outline-none">' +
            '</div>' +
            '<div>' +
              '<label for="auth-password" class="lbl block mb-1.5">Password</label>' +
              '<input id="auth-password" type="password" autocomplete="current-password" ' +
                'class="w-full bg-bg border border-line px-3 py-2.5 text-[15px] text-ink font-mono ' +
                'focus:border-info focus:outline-none">' +
            '</div>' +

            '<div id="auth-msg" class="min-h-[22px] text-[14px] ' +
              (tone === 'ok' ? 'text-ok' : 'text-bad') + '">' + esc(message || '') + '</div>' +

            '<button type="submit" id="auth-submit" ' +
              'class="w-full bg-ink text-bg font-semibold text-[15px] py-2.5 hover:bg-white ' +
              'disabled:opacity-50 disabled:cursor-not-allowed">Sign in</button>' +

            '<div class="flex items-center justify-between pt-1">' +
              '<button type="button" id="auth-magic" class="text-[14px] text-dim hover:text-ink underline underline-offset-4">' +
                'Email me a sign-in link</button>' +
              '<button type="button" id="auth-signup" class="text-[14px] text-dim hover:text-ink underline underline-offset-4">' +
                'Create account</button>' +
            '</div>' +
          '</form>' +
        '</div>' +

        '<p class="mt-4 text-[13px] text-mute">Authentication is Supabase. Configured in ' +
          '<span class="font-mono">forge/console/config.js</span>.</p>' +
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
      submitEl.textContent = on ? label : 'Sign in';
    }

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      if (!emailEl.value || !passEl.value) return say('Email and password are both required.');
      busy(true, 'Signing in…');
      client.auth.signInWithPassword({ email: emailEl.value.trim(), password: passEl.value })
        .then(function (r) {
          busy(false);
          if (r.error) return say(r.error.message);
        })
        .catch(function (err) { busy(false); say(String(err && err.message || err)); });
    });

    document.getElementById('auth-magic').addEventListener('click', function () {
      if (!emailEl.value) return say('Enter your email first.');
      say('Sending…', true);
      client.auth.signInWithOtp({ email: emailEl.value.trim() })
        .then(function (r) {
          if (r.error) return say(r.error.message);
          say('Check your email for the sign-in link.', true);
        })
        .catch(function (err) { say(String(err && err.message || err)); });
    });

    document.getElementById('auth-signup').addEventListener('click', function () {
      if (!emailEl.value || !passEl.value) return say('Email and password are both required.');
      say('Creating…', true);
      client.auth.signUp({ email: emailEl.value.trim(), password: passEl.value })
        .then(function (r) {
          if (r.error) return say(r.error.message);
          say(r.data && r.data.session ? 'Signed in.' : 'Account created. Confirm your email, then sign in.', true);
        })
        .catch(function (err) { say(String(err && err.message || err)); });
    });

    emailEl.focus();
  }

  function hideSignIn() {
    var host = document.getElementById('auth-screen');
    host.hidden = true;
    host.innerHTML = '';
  }

  // -------------------------------------------------------------------- api
  /**
   * Resolves once the console is allowed to render: either auth is off, or
   * there is a live session. Never rejects — a broken auth config degrades to
   * open mode with a visible warning rather than a blank screen.
   */
  function init() {
    return new Promise(function (resolve) {
      if (!configured()) {
        enabled = false;
        return resolve({ enabled: false, reason: qs.has('noauth') ? 'bypassed' : 'unconfigured' });
      }
      if (!window.supabase || !window.supabase.createClient) {
        enabled = false;
        console.warn('[forge] supabase-js did not load; console is running open.');
        return resolve({ enabled: false, reason: 'sdk-unavailable' });
      }

      try {
        client = window.supabase.createClient(CFG.supabaseUrl, CFG.supabaseAnonKey);
      } catch (err) {
        enabled = false;
        console.warn('[forge] supabase client failed to build; console is running open.', err);
        return resolve({ enabled: false, reason: 'client-error' });
      }
      enabled = true;

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
        renderSignIn('Could not reach Supabase. Check the URL in config.js.');
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
