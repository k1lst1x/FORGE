/*
 * forge/console/app.js — the FORGE console.
 *
 * Vanilla, buildless, one file. Reads come from forge-control, writes proxy to
 * Port. Five screens behind a hash router: #live #findings #runs #catalog #submit.
 *
 * Three rules govern everything below, because this gets watched at video
 * compression rather than read on a laptop:
 *
 *   1. Never blank the screen. A failed poll keeps the last good data and
 *      raises a reconnecting pill. Errors never reach the user as a stack trace.
 *   2. Never flicker. Screens re-render only when their data actually changes;
 *      the clocks tick by writing textContent into the nodes that hold them.
 *   3. State is colour AND a text label AND a shape, never colour alone.
 */
(function () {
  'use strict';

  var CFG = window.FORGE_CONFIG || {};
  var QS = new URLSearchParams(location.search);
  var API = (QS.get('api') || CFG.apiBase || '').replace(/\/$/, '');

  // ══════════════════════════════════════════════════════════════ constants

  var STAGES = ['INTAKE', 'CONTEXT', 'TRIAGE', 'PLAN', 'ACT', 'VERIFY', 'GATE', 'RELEASE'];
  var SKIPPABLE = ['PLAN', 'ACT', 'VERIFY', 'GATE', 'RELEASE'];
  var RETRYABLE = { PLAN: 1, ACT: 1, VERIFY: 1 };

  var CLASSES = {
    AUTOFIX_SAFE:       { c: '#34D399', acts: true,  blurb: 'Contained. The factory will write the patch.' },
    NEW_FEATURE:        { c: '#34D399', acts: true,  blurb: 'A coherent brief. The factory will build it.' },
    NEEDS_HUMAN_DESIGN: { c: '#F59E0B', acts: false, blurb: 'Real, but the fix has consequences outside this codebase.' },
    FALSE_POSITIVE:     { c: '#60A5FA', acts: false, blurb: 'The check fired but is wrong in this context.' },
    UPSTREAM_OUTAGE:    { c: '#EF4444', acts: false, blurb: 'Nothing was served, so there is nothing to fix.' },
    DUPLICATE:          { c: '#5C5C64', acts: false, blurb: 'Same root cause as a run already in flight.' },
  };

  var SEV = {
    HIGH: { c: '#EF4444', glyph: '■' },  // filled square
    MED:  { c: '#F59E0B', glyph: '▲' },  // triangle
    LOW:  { c: '#60A5FA', glyph: '●' },  // circle
  };

  var GRADES = {
    gold:   { c: '#34D399', label: 'GOLD' },
    silver: { c: '#F59E0B', label: 'SILVER' },
    bronze: { c: '#EF4444', label: 'BRONZE' },
  };

  var FSTATUS = {
    open:       { c: '#8B8B93', glyph: '○', label: 'open' },
    fixing:     { c: '#60A5FA', glyph: '◐', label: 'fixing' },
    suppressed: { c: '#5C5C64', glyph: '⊘', label: 'suppressed' },
  };

  var OUTCOMES = {
    merged:                    { c: '#34D399', label: 'merged' },
    escalated:                 { c: '#F59E0B', label: 'escalated' },
    suppressed:                { c: '#5C5C64', label: 'suppressed' },
    attached_to_existing_run:  { c: '#5C5C64', label: 'attached' },
    rejected_by_human:         { c: '#EF4444', label: 'rejected' },
    verify_failed_escalated:   { c: '#F59E0B', label: 'verify failed' },
    merge_failed:              { c: '#EF4444', label: 'merge failed' },
    backed_off:                { c: '#5C5C64', label: 'backed off' },
    error:                     { c: '#EF4444', label: 'error' },
  };

  var SCREENS = ['live', 'findings', 'runs', 'catalog', 'submit'];

  //: one glyph per section. Inline so the dashboard needs no icon dependency.
  var NAV_ICONS = (function () {
    function svg(d) {
      return '<svg width="17" height="17" viewBox="0 0 20 20" fill="none" aria-hidden="true">' +
        d + '</svg>';
    }
    return {
      live:     svg('<rect x="2.5" y="3.5" width="15" height="13" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M6 10.5l2.5 2.5L14 7.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'),
      findings: svg('<path d="M10 2.5l6.5 3v4.7c0 3.4-2.6 6.2-6.5 7.3-3.9-1.1-6.5-3.9-6.5-7.3V5.5l6.5-3z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M10 7.5v3M10 13h.01" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'),
      runs:     svg('<circle cx="10" cy="10" r="7" stroke="currentColor" stroke-width="1.5"/><path d="M10 6v4.2l2.8 1.6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'),
      catalog:  svg('<rect x="2.5" y="3.5" width="15" height="13" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M7.5 3.5v13" stroke="currentColor" stroke-width="1.5"/>'),
      submit:   svg('<path d="M10 16V4M10 4L5.5 8.5M10 4l4.5 4.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'),
    };
  })();

  var EXAMPLE_BRIEFS = [
    'Add a /pricing page with three plan tiers, a monthly/yearly toggle, and a comparison table. Every plan needs a name, a price, and a feature list.',
    'Build a /status page showing the last 90 days of uptime per service as a bar strip, with the current incident, if any, called out at the top.',
    'Add a /changelog page that lists releases newest first, each with a version, a date, and a short summary grouped into added, changed and fixed.',
    'Add a search box to the docs index that filters the page list as you type. No backend call — filter the list already on the page.',
  ];

  // ══════════════════════════════════════════════════════════════════ state

  var S = {
    screen: 'live',
    run: null,
    status: null,
    findings: null,
    runs: null,
    catalog: null,
    online: true,          // last poll succeeded
    demo: QS.has('demo'),  // serving the offline dataset
    loaded: {},            // which datasets have arrived at least once
    sigs: {},              // last rendered signature per screen
    expanded: {},          // row id -> true
    filters: { severity: 'all', route: 'all', status: 'all' },
    countdown: null,       // seconds, ticked locally between status polls
    runStart: null,        // ms epoch, for the elapsed clock
    toast: null,
    composer: null,
  };

  // ═══════════════════════════════════════════════════════════════ utilities

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function toMs(v) {
    if (v == null) return null;
    if (typeof v === 'number') return v < 1e12 ? v * 1000 : v;   // epoch seconds or ms
    var p = Date.parse(v);
    return isNaN(p) ? null : p;
  }

  function fmtMs(ms) {
    if (ms == null || isNaN(ms)) return '—';
    if (ms < 1000) return Math.round(ms) + 'ms';
    if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
    var m = Math.floor(ms / 60000);
    return m + 'm ' + Math.round((ms % 60000) / 1000) + 's';
  }

  function mmss(sec) {
    if (sec == null || isNaN(sec)) return '--:--';
    sec = Math.max(0, Math.round(sec));
    return Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0');
  }

  function rel(ms) {
    if (!ms) return '—';
    var d = Math.max(0, Date.now() - ms) / 1000;
    if (d < 60) return Math.round(d) + 's ago';
    if (d < 3600) return Math.round(d / 60) + 'm ago';
    if (d < 86400) return Math.round(d / 3600) + 'h ago';
    return Math.round(d / 86400) + 'd ago';
  }

  function tint(hex, alpha) {
    var n = parseInt(hex.slice(1), 16);
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + alpha + ')';
  }

  function portUrl(kind, id) {
    var tpl = CFG['port' + kind + 'Url'] || '';
    return id && tpl ? tpl.replace('{id}', encodeURIComponent(id)) : null;
  }

  function traceUrl(id) {
    return id && CFG.signozTraceUrl ? CFG.signozTraceUrl.replace('{id}', encodeURIComponent(id)) : null;
  }

  /** External link that never renders as a dead control when there is no URL. */
  function link(href, label, cls) {
    if (!href) {
      return '<span class="' + (cls || '') + ' opacity-40 cursor-not-allowed" ' +
        'title="Not available for this run">' + esc(label) + '</span>';
    }
    return '<a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer" ' +
      'class="' + (cls || '') + '">' + esc(label) + '</a>';
  }

  function chip(text, color, glyph, extra) {
    return '<span class="inline-flex items-center gap-1.5 border px-2 py-0.5 text-[13px] whitespace-nowrap ' +
      (extra || '') + '" style="color:' + color + ';border-color:' + tint(color, 0.45) +
      ';background:' + tint(color, 0.08) + '">' +
      (glyph ? '<span aria-hidden="true" class="text-[10px]">' + glyph + '</span>' : '') +
      esc(text) + '</span>';
  }

  function sevChip(sev) {
    var s = SEV[String(sev || '').toUpperCase()] || SEV.LOW;
    return chip(String(sev || '?').toUpperCase(), s.c, s.glyph, 'font-semibold tracking-wide');
  }

  function gradeChip(grade, big) {
    var g = GRADES[String(grade || '').toLowerCase()] || { c: '#5C5C64', label: String(grade || '?').toUpperCase() };
    return '<span class="inline-flex items-center border font-semibold tracking-[0.09em] ' +
      (big ? 'px-3 py-1 text-[15px]' : 'px-2 py-0.5 text-[13px]') + '" ' +
      'style="color:' + g.c + ';border-color:' + tint(g.c, 0.45) + ';background:' + tint(g.c, 0.08) + '">' +
      esc(g.label) + '</span>';
  }

  function intakeBadge(intake) {
    var brief = intake === 'brief';
    var c = brief ? '#60A5FA' : '#F59E0B';
    return chip(brief ? 'BRIEF' : 'FINDING', c, brief ? '◆' : '▲', 'font-bold tracking-[0.09em]');
  }

  function skeleton(rows) {
    var out = '';
    for (var i = 0; i < rows; i++) {
      out += '<div class="sk h-4 mb-3" style="width:' + (95 - (i % 4) * 14) + '%"></div>';
    }
    return '<div class="border border-line bg-surface p-6">' + out + '</div>';
  }

  function emptyState(title, body, actionLabel, actionKey) {
    return '<div class="frame border border-line bg-surface px-8 py-14 text-center">' +
      '<h2 class="text-[26px] font-semibold text-ink">' + esc(title) + '</h2>' +
      '<p class="mt-2 text-[16px] text-dim max-w-[560px] mx-auto leading-relaxed">' + body + '</p>' +
      (actionLabel
        ? '<button data-act="' + actionKey + '" class="mt-7 border border-line bg-raise px-5 py-2.5 ' +
          'text-[15px] font-semibold text-ink hover:border-info hover:bg-surface">' + esc(actionLabel) + '</button>'
        : '') +
      '</div>';
  }

  // ═════════════════════════════════════════════════════════════════ the API

  var inflight = {};

  function request(key, path, opts) {
    if (inflight[key]) return Promise.resolve(undefined);   // never stack requests
    var ctrl = new AbortController();
    inflight[key] = ctrl;
    var timer = setTimeout(function () { ctrl.abort(); }, 8000);

    var headers = { 'Accept': 'application/json' };
    var token = window.ForgeAuth && window.ForgeAuth.token();
    if (token) headers['Authorization'] = 'Bearer ' + token;
    if (opts && opts.body) headers['Content-Type'] = 'application/json';

    return fetch(API + path, {
      method: (opts && opts.method) || 'GET',
      headers: headers,
      body: opts && opts.body ? JSON.stringify(opts.body) : undefined,
      signal: ctrl.signal,
      cache: 'no-store',
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var type = r.headers.get('content-type') || '';
      return type.indexOf('json') >= 0 ? r.json() : r.text();
    }).finally(function () {
      clearTimeout(timer);
      delete inflight[key];
    });
  }

  /** Unwrap the common envelope shapes so forge-control can pick either. */
  function unwrap(payload, key) {
    if (payload == null) return null;
    if (Array.isArray(payload)) return payload;
    if (key && Object.prototype.hasOwnProperty.call(payload, key)) return payload[key];
    if (Object.prototype.hasOwnProperty.call(payload, 'data')) return payload.data;
    return payload;
  }

  // ─────────────────────────────────────────────────────────── normalisers
  // forge-control is being written in another session. These accept both the
  // ChangeRequest shape from forge/models.py and the flatter shapes an API
  // layer tends to produce, so the console does not break on either.

  function normRun(raw) {
    var r = unwrap(raw, 'run');
    if (!r || !r.run_id) return null;

    var t = r.triage || {};
    var finding = r.finding || null;
    var ctx = r.context || {};

    return {
      run_id: r.run_id,
      intake: r.intake || 'finding',
      trigger: r.trigger || ctx.trigger || (r.intake === 'brief' ? 'port brief' : 'scheduler'),
      title: r.title || '(untitled run)',
      stage: String(r.stage || 'INTAKE').toUpperCase(),
      status: r.status || 'running',
      attempts: r.attempts || 0,
      trace_id: r.trace_id || null,
      started_at: toMs(r.started_at != null ? r.started_at : r.created_at),
      finished_at: toMs(r.finished_at),
      outcome: r.outcome || null,

      classification: r.classification || t.classification || null,
      should_act: r.should_act != null ? r.should_act : (t.should_act != null ? t.should_act : null),
      justification: r.justification || t.justification || null,
      confidence: r.confidence != null ? r.confidence : (t.confidence != null ? t.confidence : null),
      decided_by: r.decided_by || t.decided_by || null,
      blast_radius: r.blast_radius || t.blast_radius || null,
      model: r.model || t.model || null,
      tokens_in: r.tokens_in != null ? r.tokens_in : t.tokens_in,
      tokens_out: r.tokens_out != null ? r.tokens_out : t.tokens_out,

      brief_text: r.brief_text || null,
      finding: finding ? {
        finding_id: finding.finding_id || null,
        check_id: finding.check_id || null,
        severity: finding.severity || null,
        route: finding.route || null,
        evidence: finding.evidence || null,
        title: finding.title || null,
      } : null,
      route: (finding && finding.route) || ctx.route || null,

      changeset: (r.changeset || []).map(function (c) {
        return {
          path: c.path || '(unnamed file)',
          added: c.added != null ? c.added : (c.lines_added != null ? c.lines_added : null),
          removed: c.removed != null ? c.removed : (c.lines_removed != null ? c.lines_removed : null),
          reason: c.reason || null,
        };
      }),
      verify: normVerify(r.verify),
      verify_failure: r.verify_failure || (r.verify && r.verify.ok === false ? r.verify.evidence : null) || null,
      pr_url: r.pr_url || null,
      issue_url: r.issue_url || null,
      approval_id: r.approval_id || null,
      stages: r.stages || null,
    };
  }

  function normVerify(v) {
    if (!v || (v.ok === false && !v.tests_total)) return null;
    if (v.ok === false) return null;   // a failed verify is shown as a failure, not a pass
    var closed = v.closed != null ? v.closed : (v.findings_closed || []).length;
    var introduced = v.introduced != null ? v.introduced : (v.findings_introduced || []).length;
    var passed = v.tests_passed, total = v.tests_total;
    if (typeof passed === 'boolean') { total = passed ? 'pass' : 'fail'; passed = null; }
    return { passed: passed, total: total, closed: closed, introduced: introduced, evidence: v.evidence || null };
  }

  function normFinding(f) {
    return {
      finding_id: f.finding_id || f.id || null,
      check_id: f.check_id || '?',
      severity: String(f.severity || 'LOW').toUpperCase(),
      route: f.route || '/',
      title: f.title || f.check_id || 'Finding',
      status: f.status || 'open',
      occurrences: f.occurrences || 1,
      evidence: f.evidence || '',
      justification: f.justification || null,
      hint: f.suggested_fix_hint || null,
      first_seen: toMs(f.first_seen),
      run_id: f.run_id || null,
    };
  }

  function normStatus(s) {
    if (!s) return null;
    var sev = s.severity || s.severity_counts || {};
    return {
      scheduler: s.scheduler || (s.scheduler_healthy === false ? 'down' : 'healthy'),
      next_audit_seconds: s.next_audit_seconds != null ? s.next_audit_seconds : s.next_audit_in,
      runs_today: s.runs_today != null ? s.runs_today : 0,
      HIGH: sev.HIGH || sev.high || 0,
      MED: sev.MED || sev.med || 0,
      LOW: sev.LOW || sev.low || 0,
      grades: s.grades || {},
      runs_per_hour: s.runs_per_hour || [],
    };
  }

  function normHistory(r) {
    return {
      run_id: r.run_id,
      started_at: toMs(r.started_at != null ? r.started_at : r.created_at),
      intake: r.intake || 'finding',
      trigger: r.trigger || (r.intake === 'brief' ? 'port brief' : 'scheduler'),
      title: r.title || '(untitled run)',
      classification: r.classification || null,
      outcome: r.outcome || null,
      duration_ms: r.duration_ms != null ? r.duration_ms : null,
      attempts: r.attempts || 0,
      trace_id: r.trace_id || null,
      pr_url: r.pr_url || null,
      stages: r.stages || null,
    };
  }

  function normCatalog(p) {
    return {
      route: p.route || '/',
      title: p.title || p.route || 'Page',
      grade: String(p.grade || 'gold').toLowerCase(),
      high: p.high != null ? p.high : 0,
      med: p.med != null ? p.med : 0,
      last_audit: toMs(p.last_audit),
      created_by_run: p.created_by_run || p.run_id || null,
      page_id: p.page_id || p.route || null,
    };
  }

  // ═══════════════════════════════════════════════════════════════ polling

  function pollStatus() {
    // The status poll doubles as the liveness probe: if the API answers while
    // the console is on the offline dataset, it switches itself back to live.
    if (QS.has('demo')) return applyDemoStatus();

    request('status', '/api/status').then(function (payload) {
      if (payload === undefined) return;
      var s = normStatus(unwrap(payload, 'status'));
      if (!s) throw new Error('empty status');
      if (S.demo) {
        S.demo = false;
        console.info('[forge] forge-control is reachable again — back on live data.');
        pollCurrent(); pollFindings(); pollRuns(); pollCatalog();
      }
      S.status = s;
      S.countdown = s.next_audit_seconds;
      S.loaded.status = true;
      S.online = true;
      renderStatusBar();
      renderRail();
    }).catch(function () {
      if (!S.loaded.status && !QS.has('nodemo')) enterDemo();
      else if (S.demo) applyDemoStatus();
      else markOnline(false);
    });
  }

  function applyDemoStatus() {
    S.status = normStatus(window.FORGE_DEMO.status());
    S.countdown = S.status.next_audit_seconds;
    S.loaded.status = true;
    S.online = true;               // the offline dataset is not a connection fault
    renderStatusBar();
    renderRail();
  }

  function pollCurrent() {
    if (S.demo) {
      // Through the same normaliser as the live payload, so the offline path
      // exercises exactly the code the real one does.
      applyRun(normRun(window.FORGE_DEMO.currentRun()));
      return;
    }
    request('current', '/api/runs/current').then(function (payload) {
      if (payload === undefined) return;
      markOnline(true);
      S.loaded.run = true;
      applyRun(normRun(payload));
    }).catch(function () { markOnline(false); });
  }

  function applyRun(run) {
    var prevId = S.run && S.run.run_id;
    S.run = run;
    S.loaded.run = true;
    if (run && run.run_id !== prevId) S.runStart = run.started_at || Date.now();
    if (!run) S.runStart = null;
    if (S.screen === 'live') renderScreen();
  }

  function pollFindings() {
    if (S.demo) {
      S.findings = window.FORGE_DEMO.findings().map(normFinding);
      S.loaded.findings = true;
      if (S.screen === 'findings') renderScreen();
      return;
    }
    request('findings', '/api/findings').then(function (payload) {
      if (payload === undefined) return;
      var rows = unwrap(payload, 'findings') || [];
      S.findings = (Array.isArray(rows) ? rows : []).map(normFinding);
      S.loaded.findings = true;
      markOnline(true);
      if (S.screen === 'findings') renderScreen();
    }).catch(function () { markOnline(false); });
  }

  function pollRuns() {
    if (S.demo) {
      S.runs = window.FORGE_DEMO.runs().map(normHistory);
      S.loaded.runs = true;
      if (S.screen === 'runs') renderScreen();
      return;
    }
    request('runs', '/api/runs?limit=20').then(function (payload) {
      if (payload === undefined) return;
      var rows = unwrap(payload, 'runs') || [];
      S.runs = (Array.isArray(rows) ? rows : []).map(normHistory);
      S.loaded.runs = true;
      markOnline(true);
      if (S.screen === 'runs') renderScreen();
    }).catch(function () { markOnline(false); });
  }

  function pollCatalog() {
    if (S.demo) {
      S.catalog = window.FORGE_DEMO.catalog().map(normCatalog);
      S.loaded.catalog = true;
      if (S.screen === 'catalog') renderScreen();
      return;
    }
    request('catalog', '/api/catalog').then(function (payload) {
      if (payload === undefined) return;
      var rows = unwrap(payload, 'pages') || unwrap(payload, 'catalog') || [];
      S.catalog = (Array.isArray(rows) ? rows : []).map(normCatalog);
      S.loaded.catalog = true;
      markOnline(true);
      if (S.screen === 'catalog') renderScreen();
    }).catch(function () { markOnline(false); });
  }

  function markOnline(ok) {
    if (S.online === ok) return;
    S.online = ok;
    renderStatusBar();
    renderRail();
  }

  function enterDemo() {
    if (S.demo) return;
    S.demo = true;
    console.warn('[forge] forge-control is not reachable at "' + (API || location.origin) +
      '". Serving the offline dataset; the status bar says so.');
    applyDemoStatus();
    pollCurrent(); pollFindings(); pollRuns(); pollCatalog();
  }

  function refreshAll() {
    pollStatus();
    pollCurrent();
    pollFindings();
    pollRuns();
    pollCatalog();
  }

  // ══════════════════════════════════════════════════════════════════ chrome

  function renderRail() {
    var nav = document.getElementById('nav');
    var titles = { live: 'Live', findings: 'Findings', runs: 'Runs', catalog: 'Catalog', submit: 'Submit' };

    // The nav only changes on four inputs. Rebuilding it on every 3s status
    // poll would repaint the rail under a judge's cursor for no reason.
    var navSig = [S.screen, S.status && S.status.HIGH, !!S.run,
      window.ForgeAuth && !!window.ForgeAuth.user()].join('|');
    if (S.sigs.nav === navSig) return renderRailHealth();
    S.sigs.nav = navSig;

    nav.innerHTML = SCREENS.map(function (id) {
      var on = S.screen === id;
      var badge = '';
      if (id === 'findings' && S.status && S.status.HIGH > 0) {
        badge = '<span class="ml-auto rounded-full px-2 py-0.5 text-[12px] font-semibold" ' +
          'style="color:#EF4444;background:rgba(239,68,68,.13)">' + S.status.HIGH + '</span>';
      }
      if (id === 'live' && S.run) {
        badge = '<span class="ml-auto h-1.5 w-1.5 rounded-full dot-live" style="background:#34D399"></span>';
      }
      return '<a href="#' + id + '" class="group flex items-center gap-3 rounded-lg px-3 py-2.5 text-[15px] ' +
        (on ? 'bg-white/[0.07] text-ink' : 'text-dim hover:bg-white/[0.04] hover:text-ink') + '">' +
        '<span class="' + (on ? 'text-brand' : 'text-mute') + '">' + NAV_ICONS[id] + '</span>' +
        '<span>' + titles[id] + '</span>' + badge + '</a>';
    }).join('');

    document.title = 'FORGE — ' + titles[S.screen];
    var pt = document.getElementById('page-title');
    if (pt) pt.textContent = titles[S.screen];

    renderSideSeverity();
    renderRailHealth();
  }

  /** The severity card in the sidebar. Same three numbers as the status bar,
   *  kept visible while you are deep in another screen. */
  function renderSideSeverity() {
    var host = document.getElementById('side-sev');
    if (!host || !S.status) return;
    var st = S.status;
    host.innerHTML = [['HIGH', st.HIGH, '#EF4444'], ['MED', st.MED, '#F59E0B'], ['LOW', st.LOW, '#60A5FA']]
      .map(function (r) {
        return '<div><div class="text-[22px] font-semibold leading-none tabular-nums" style="color:' +
          (r[1] > 0 ? r[2] : '#5C5C64') + '">' + r[1] + '</div>' +
          '<div class="lbl mt-1 text-[10px]">' + r[0] + '</div></div>';
      }).join('');
  }

  /** Cheap enough to run on every poll: writes into nodes, never replaces them. */
  function renderRailHealth() {
    var healthy = S.status && S.status.scheduler === 'healthy';
    var c = !S.online ? '#F59E0B' : healthy ? '#34D399' : '#EF4444';
    var text = !S.online ? 'reconnecting' : healthy ? 'scheduler ok' : (S.status ? 'scheduler down' : 'connecting');
    var dot = document.querySelector('[data-health-dot]');
    var label = document.querySelector('[data-health-text]');
    if (dot) {
      dot.style.background = c;
      dot.className = 'inline-block w-2 h-2 rounded-full' + (S.online && healthy ? ' dot-live' : '');
    }
    if (label) { label.textContent = text; label.style.color = c; }

    var userBox = document.getElementById('user-menu');
    var user = window.ForgeAuth && window.ForgeAuth.user();
    if (!userBox) return;

    var sig = user ? 'u:' + user.email : (window.ForgeAuth && window.ForgeAuth.enabled() ? 'anon' : 'off');
    if (S.sigs.user === sig) return;
    S.sigs.user = sig;

    if (user) {
      var email = user.email || 'signed in';
      var initial = email.charAt(0).toUpperCase();
      userBox.innerHTML =
        '<button data-act="user-toggle" class="flex items-center gap-2.5 rounded-full border ' +
          'border-white/[0.09] bg-white/[0.03] py-1 pl-1 pr-3 hover:border-white/30">' +
          '<span class="flex h-7 w-7 items-center justify-center rounded-full bg-white/10 ' +
            'text-[12px] font-semibold">' + esc(initial) + '</span>' +
          '<span class="max-w-[150px] truncate text-[13px] text-dim">' + esc(email) + '</span>' +
        '</button>' +
        '<div data-user-pop hidden class="absolute right-0 top-11 z-50 w-[240px] rounded-xl ' +
          'border border-white/[0.09] bg-[#0C0C0F] p-1.5 shadow-2xl">' +
          '<div class="px-3 py-2.5 border-b border-white/[0.07]">' +
            '<div class="text-[13px] text-ink truncate">' + esc(email) + '</div>' +
            '<div class="lbl mt-1 text-[10px]">Signed in</div>' +
          '</div>' +
          '<button data-act="signout" class="mt-1 w-full rounded-lg px-3 py-2 text-left ' +
            'text-[14px] text-dim hover:bg-white/[0.05] hover:text-ink">Sign out</button>' +
        '</div>';
    } else if (window.ForgeAuth && !window.ForgeAuth.enabled()) {
      userBox.innerHTML = '<span class="rounded-full border border-white/[0.09] px-3 py-1.5 ' +
        'text-[12px] text-mute" title="Set supabaseUrl and supabaseAnonKey in config.js">' +
        'auth not configured</span>';
    }
  }

  function statTile(value, label, color, ring) {
    return '<div class="px-4 py-2 ' + (ring ? 'border' : '') + '" ' +
      (ring ? 'style="border-color:' + tint(color, 0.5) + ';background:' + tint(color, 0.07) + '"' : '') + '>' +
      '<div class="text-[30px] leading-none font-semibold tabular-nums" style="color:' + color + '">' +
        esc(String(value)) + '</div>' +
      '<div class="lbl mt-1.5 text-[12px]">' + esc(label) + '</div>' +
    '</div>';
  }

  function renderStatusBar() {
    var host = document.getElementById('statusbar');
    if (!S.loaded.status) {
      if (S.sigs.bar === 'sk') return;
      S.sigs.bar = 'sk';
      host.innerHTML = '<div class="border border-line bg-surface p-5 flex gap-10">' +
        '<div class="sk h-12 w-24"></div><div class="sk h-12 w-24"></div>' +
        '<div class="sk h-12 w-20"></div><div class="sk h-12 w-20"></div></div>';
      return;
    }
    var st = S.status;

    // Deliberately excludes the countdown: it ticks by textContent, below.
    var barSig = [st.runs_today, st.HIGH, st.MED, st.LOW, S.online, S.demo,
      JSON.stringify(st.grades)].join('|');
    if (S.sigs.bar === barSig) return;
    S.sigs.bar = barSig;

    var pills = '';
    if (!S.online) {
      pills += '<span class="inline-flex items-center gap-2 border px-3 py-1 text-[14px]" ' +
        'style="color:#F59E0B;border-color:' + tint('#F59E0B', 0.5) + ';background:' + tint('#F59E0B', 0.09) + '">' +
        '<span class="w-1.5 h-1.5 rounded-full dot-live" style="background:#F59E0B"></span>' +
        'reconnecting — showing last known state</span>';
    }
    if (S.demo) {
      pills += '<span class="inline-flex items-center gap-2 border px-3 py-1 text-[14px] ml-2" ' +
        'style="color:#F59E0B;border-color:' + tint('#F59E0B', 0.5) + ';background:' + tint('#F59E0B', 0.09) + '">' +
        '⊘ DEMO DATA — forge-control not reachable</span>';
    }

    var grades = Object.keys(st.grades || {}).sort();
    var gradeHtml = grades.length
      ? grades.map(function (route) {
          return '<span class="inline-flex items-center gap-2">' +
            '<span class="font-mono text-[13px] text-dim">' + esc(route) + '</span>' +
            gradeChip(st.grades[route]) + '</span>';
        }).join('')
      : '<span class="text-[14px] text-mute">no routes graded yet</span>';

    host.innerHTML =
      '<div class="border border-line bg-surface">' +
        '<div class="flex items-stretch flex-wrap gap-x-2 gap-y-4 p-5">' +
          '<div class="px-4 py-2">' +
            '<div class="text-[30px] leading-none font-semibold font-mono tabular-nums text-ink" ' +
              'data-tick="countdown">' + mmss(S.countdown) + '</div>' +
            '<div class="lbl mt-1.5 text-[12px]">Next audit</div>' +
          '</div>' +
          '<div class="w-px bg-line mx-2"></div>' +
          statTile(st.runs_today, 'Runs today', '#FFFFFF', false) +
          '<div class="w-px bg-line mx-2"></div>' +
          statTile(st.HIGH, 'High', '#EF4444', st.HIGH > 0) +
          statTile(st.MED, 'Med', '#F59E0B', false) +
          statTile(st.LOW, 'Low', '#60A5FA', false) +
          '<div class="ml-auto flex flex-col justify-center items-end gap-2 pl-6">' +
            '<div class="lbl text-[12px]">Route grades</div>' +
            '<div class="flex flex-wrap items-center justify-end gap-x-4 gap-y-2">' + gradeHtml + '</div>' +
          '</div>' +
        '</div>' +
        (pills ? '<div class="border-t border-line px-5 py-2.5">' + pills + '</div>' : '') +
      '</div>';
  }

  // ═════════════════════════════════════════════════════ SCREEN 1 — LIVE

  function deriveStages(run) {
    // forge-control may send per-stage detail. If it does not, derive what we
    // can from the single `stage` field rather than showing nothing.
    if (run.stages) return run.stages;
    var out = {};
    var here = STAGES.indexOf(run.stage);
    var terminal = ['done', 'failed', 'rejected', 'escalated'].indexOf(run.status) >= 0;
    STAGES.forEach(function (name, i) {
      if (here < 0) { out[name] = { status: 'pending' }; return; }
      if (i < here) out[name] = { status: 'done' };
      else if (i === here) out[name] = { status: terminal ? 'done' : 'active' };
      else out[name] = { status: 'pending' };
    });
    if (run.should_act === false && here >= STAGES.indexOf('TRIAGE')) {
      SKIPPABLE.forEach(function (n) { out[n] = { status: 'skipped' }; });
    }
    return out;
  }

  function pipelineNode(name, info, isLast) {
    var st = info.status || 'pending';
    var box, glyph, glyphColor, labelCls = '', beneath;

    if (st === 'done') {
      box = 'border-ok/50 bg-surface';
      glyph = '✓'; glyphColor = '#34D399';
      beneath = '<span class="font-mono text-[12px] text-dim">' + fmtMs(info.duration_ms) + '</span>';
    } else if (st === 'active') {
      box = 'bg-raise stage-active';
      glyph = '◆'; glyphColor = '#60A5FA';
      beneath = '<span class="font-mono text-[12px]" style="color:#60A5FA" data-tick="stage-dur">' +
        fmtMs(info.duration_ms) + '</span>';
    } else if (st === 'skipped') {
      box = 'border-line bg-transparent opacity-40';
      glyph = '⊘'; glyphColor = '#5C5C64';
      labelCls = 'line-through';
      beneath = '<span class="text-[12px] text-mute">skipped</span>';
    } else {
      box = 'border-line bg-transparent';
      glyph = '○'; glyphColor = '#5C5C64';
      beneath = '<span class="text-[12px] text-mute">pending</span>';
    }

    var attempts = info.attempts && info.attempts > 1
      ? '<span class="ml-1.5 font-mono text-[12px] font-semibold" style="color:#F59E0B" ' +
        'title="attempt ' + info.attempts + '">×' + info.attempts + '</span>'
      : '';

    var border = st === 'active' ? 'border' : 'border';
    var style = st === 'active' ? 'style="border-color:#60A5FA"' : '';

    return '<div class="flex-1 min-w-0">' +
      '<div class="' + border + ' ' + box + ' px-2 py-3 text-center" ' + style + '>' +
        '<div class="flex items-center justify-center gap-1.5">' +
          '<span aria-hidden="true" class="text-[13px]" style="color:' + glyphColor + '">' + glyph + '</span>' +
          '<span class="lbl text-[12px] ' + labelCls + '" ' +
            (st === 'done' || st === 'active' ? 'style="color:#FFFFFF"' : '') + '>' + name + '</span>' +
          attempts +
        '</div>' +
        '<div class="mt-1.5">' + beneath + '</div>' +
      '</div>' +
    '</div>' +
    (isLast ? '' : '<div class="w-5 h-px shrink-0 self-center" style="background:' +
      (st === 'done' ? '#34D399' : st === 'skipped' ? '#1F2937' : '#1F2937') + '"></div>');
  }

  function renderPipeline(run) {
    var stages = deriveStages(run);
    var nodes = STAGES.map(function (name, i) {
      return pipelineNode(name, stages[name] || { status: 'pending' }, i === STAGES.length - 1);
    }).join('');
    return '<section>' +
      '<div class="lbl mb-3">Pipeline</div>' +
      '<div class="flex items-stretch">' + nodes + '</div>' +
    '</section>';
  }

  function renderVerdict(run) {
    if (!run.classification) {
      return '<section class="border border-line bg-surface" style="min-height:180px">' +
        '<div class="h-full flex flex-col justify-center px-8 py-10">' +
          '<div class="lbl">Triage verdict</div>' +
          '<div class="mt-3 text-[24px] text-mute">Not yet decided.</div>' +
          '<div class="mt-1.5 text-[16px] text-dim">' +
            'The classification and the reasoning behind it appear here the moment TRIAGE completes.' +
          '</div>' +
        '</div>' +
      '</section>';
    }

    var meta = CLASSES[run.classification] || { c: '#5C5C64', acts: run.should_act !== false, blurb: '' };
    var conf = run.confidence != null ? Math.round(run.confidence * 100) : null;

    var right = '<div class="shrink-0 w-[190px] pl-8 border-l border-line">' +
      '<div class="lbl text-[12px]">Confidence</div>' +
      (conf != null
        ? '<div class="mt-1 text-[38px] leading-none font-semibold tabular-nums" style="color:' + meta.c + '">' +
            conf + '<span class="text-[20px]">%</span></div>' +
          '<div class="mt-3 h-1 w-full" style="background:' + tint(meta.c, 0.18) + '">' +
            '<div class="h-1" style="width:' + conf + '%;background:' + meta.c + '"></div></div>'
        : '<div class="mt-2 text-[18px] text-mute">not reported</div>') +
      (run.blast_radius
        ? '<div class="mt-5"><div class="lbl text-[12px]">Blast radius</div>' +
          '<div class="mt-1 text-[15px] text-ink font-mono">' + esc(run.blast_radius) + '</div></div>'
        : '') +
      (run.decided_by
        ? '<div class="mt-4"><div class="lbl text-[12px]">Decided by</div>' +
          '<div class="mt-1 text-[15px] text-ink font-mono">' + esc(run.decided_by) + '</div>' +
          (run.model ? '<div class="mt-0.5 text-[12px] text-mute font-mono truncate" title="' + esc(run.model) + '">' +
            esc(run.model) + '</div>' : '') +
          '</div>'
        : '') +
    '</div>';

    var refusal = run.should_act === false
      ? '<div class="border-t px-8 py-4 flex items-center gap-3" ' +
          'style="border-color:' + tint(meta.c, 0.35) + ';background:' + tint(meta.c, 0.06) + '">' +
          '<span aria-hidden="true" class="text-[16px]" style="color:' + meta.c + '">⊘</span>' +
          '<span class="text-[17px] font-semibold text-ink">No code will be written.</span>' +
          '<span class="text-[17px] text-dim">Escalated to a human' +
            (run.issue_url ? ' — issue opened' : '') + '.</span>' +
          (run.issue_url
            ? link(run.issue_url, 'Open issue ↗', 'ml-auto text-[15px] text-dim hover:text-ink underline underline-offset-4')
            : '') +
        '</div>'
      : '';

    return '<section class="border" style="border-color:' + tint(meta.c, 0.35) + ';background:' + tint(meta.c, 0.06) + '">' +
      '<div class="flex" style="min-height:180px">' +
        '<div style="width:6px;background:' + meta.c + '" aria-hidden="true"></div>' +
        '<div class="flex-1 flex px-8 py-7 gap-8">' +
          '<div class="flex-1 min-w-0">' +
            '<div class="lbl text-[12px]">Triage verdict</div>' +
            '<h2 class="mt-2 text-[40px] leading-[1.05] font-bold tracking-tight uppercase break-words" ' +
              'style="color:' + meta.c + '">' + esc(run.classification) + '</h2>' +
            '<p class="mt-4 text-[20px] leading-[1.5] text-ink max-w-[860px]">' +
              esc(run.justification || meta.blurb) + '</p>' +
          '</div>' +
          right +
        '</div>' +
      '</div>' +
      refusal +
    '</section>';
  }

  function renderEvidence(run) {
    var blocks = [];

    if (run.finding) {
      var f = run.finding;
      blocks.push(
        '<div class="flex flex-wrap items-center gap-3 mb-4">' +
          sevChip(f.severity) +
          '<span class="font-mono text-[15px] text-ink">' + esc(f.check_id) + '</span>' +
          '<span class="text-dim">on</span>' +
          '<span class="font-mono text-[15px] text-ink">' + esc(f.route || '—') + '</span>' +
          (f.finding_id ? '<span class="font-mono text-[13px] text-mute">' + esc(f.finding_id) + '</span>' : '') +
        '</div>' +
        (f.evidence
          ? '<pre class="border border-line bg-bg px-4 py-3 font-mono text-[14px] leading-relaxed ' +
            'text-dim whitespace-pre-wrap break-words">' + esc(f.evidence) + '</pre>'
          : '')
      );
    } else if (run.brief_text) {
      blocks.push('<p class="text-[17px] leading-relaxed text-ink max-w-[900px]">' + esc(run.brief_text) + '</p>');
    }

    if (run.changeset && run.changeset.length) {
      blocks.push(
        '<div class="mt-6">' +
          '<div class="lbl mb-3">Changeset · ' + run.changeset.length + ' file' +
            (run.changeset.length === 1 ? '' : 's') + '</div>' +
          '<div class="border border-line divide-y divide-line">' +
            run.changeset.map(function (c) {
              var counts = (c.added != null || c.removed != null)
                ? '<span class="font-mono text-[14px] shrink-0">' +
                    '<span style="color:#34D399">+' + (c.added || 0) + '</span> ' +
                    '<span style="color:#EF4444">−' + (c.removed || 0) + '</span></span>'
                : (c.reason
                    ? '<span class="text-[14px] text-mute truncate max-w-[380px]">' + esc(c.reason) + '</span>'
                    : '<span class="text-[14px] text-mute">modified</span>');
              return '<div class="flex items-center justify-between gap-4 px-4 py-2.5 bg-bg">' +
                '<span class="font-mono text-[14px] text-ink truncate">' + esc(c.path) + '</span>' + counts +
              '</div>';
            }).join('') +
          '</div>' +
        '</div>'
      );
    }

    if (run.verify) {
      var v = run.verify;
      var testsLabel = v.passed != null ? 'tests ' + v.passed + '/' + v.total
        : v.total === 'pass' ? 'tests passed' : 'tests failed';
      var testsOk = v.passed != null ? v.passed === v.total : v.total === 'pass';
      blocks.push(
        '<div class="mt-6">' +
          '<div class="lbl mb-3">Verification</div>' +
          '<div class="flex flex-wrap gap-3">' +
            chip(testsLabel, testsOk ? '#34D399' : '#EF4444', testsOk ? '✓' : '✗', 'text-[15px] px-3 py-1.5') +
            chip('findings closed ' + v.closed + ' · introduced ' + v.introduced,
              v.introduced > 0 ? '#EF4444' : '#34D399',
              v.introduced > 0 ? '✗' : '✓', 'text-[15px] px-3 py-1.5') +
          '</div>' +
          (v.evidence ? '<p class="mt-3 text-[15px] text-dim">' + esc(v.evidence) + '</p>' : '') +
        '</div>'
      );
    } else if (run.verify_failure) {
      blocks.push(
        '<div class="mt-6">' +
          '<div class="lbl mb-3">Verification</div>' +
          chip('verify failed · retrying with more information', '#F59E0B', '↻', 'text-[15px] px-3 py-1.5') +
          '<pre class="mt-3 border px-4 py-3 font-mono text-[14px] leading-relaxed whitespace-pre-wrap break-words" ' +
            'style="color:#F59E0B;border-color:' + tint('#F59E0B', 0.35) + ';background:' + tint('#F59E0B', 0.06) + '">' +
            esc(run.verify_failure) + '</pre>' +
        '</div>'
      );
    }

    if (!blocks.length) return '';
    return '<section class="border border-line bg-surface p-7">' +
      '<div class="lbl mb-4">Evidence</div>' + blocks.join('') + '</section>';
  }

  function renderGate(run) {
    var stages = deriveStages(run);
    if (!stages.GATE || stages.GATE.status !== 'active') return '';
    return '<section class="border" style="border-color:' + tint('#F59E0B', 0.4) + ';background:' + tint('#F59E0B', 0.07) + '">' +
      '<div class="px-8 py-7 flex items-center gap-8 flex-wrap">' +
        '<div class="flex-1 min-w-[420px]">' +
          '<div class="flex items-center gap-3">' +
            '<span class="w-2 h-2 rounded-full dot-live" style="background:#F59E0B"></span>' +
            '<span class="lbl text-[12px]" style="color:#F59E0B">Gate</span>' +
          '</div>' +
          '<h2 class="mt-2 text-[28px] font-semibold text-ink">Waiting for human approval</h2>' +
          '<p class="mt-2 text-[16px] text-dim max-w-[640px] leading-relaxed">' +
            'The change is written, tested and re-audited. Nothing merges until a human approves it. ' +
            '<span class="text-ink">Approval happens in Port, not in this console</span> — this screen ' +
            'watches the decision, it does not make it.' +
          '</p>' +
          (run.pr_url
            ? '<div class="mt-4 font-mono text-[14px] text-dim">' + link(run.pr_url, run.pr_url,
                'text-dim hover:text-ink underline underline-offset-4 break-all') + '</div>'
            : '') +
        '</div>' +
        '<div class="flex flex-col gap-3">' +
          link(portUrl('Approval', run.approval_id) || portUrl('Run', run.run_id),
            'Review in Port ↗',
            'inline-flex items-center justify-center bg-ink text-bg font-semibold text-[16px] px-7 py-3.5 hover:bg-white') +
          link(run.pr_url, 'Open pull request ↗',
            'inline-flex items-center justify-center border border-line text-[15px] px-7 py-2.5 text-dim hover:text-ink hover:border-dim') +
        '</div>' +
      '</div>' +
    '</section>';
  }

  function renderLive() {
    if (!S.loaded.run) return skeleton(4);

    var run = S.run;
    if (!run) {
      return emptyState(
        'No active run',
        'The factory is idle. The scheduler opens the next audit in ' +
          '<span class="font-mono text-ink" data-tick="countdown">' + mmss(S.countdown) + '</span>, ' +
          'or you can start one now.',
        'Run audit now', 'audit-now');
    }

    var elapsed = S.runStart ? (Date.now() - S.runStart) : null;

    var header = '<section class="flex items-start gap-6 flex-wrap">' +
      '<div class="flex-1 min-w-[520px]">' +
        '<div class="flex items-center gap-3 flex-wrap">' +
          intakeBadge(run.intake) +
          '<span class="font-mono text-[14px] text-dim">' + esc(run.run_id) + '</span>' +
          '<span class="text-mute">·</span>' +
          '<span class="font-mono text-[14px] text-dim">' + esc(run.trigger) + '</span>' +
        '</div>' +
        '<h1 class="mt-3 text-[30px] leading-tight font-semibold text-ink">' + esc(run.title) + '</h1>' +
      '</div>' +
      '<div class="flex items-center gap-6">' +
        '<div class="text-right">' +
          '<div class="text-[30px] leading-none font-semibold font-mono tabular-nums text-ink" ' +
            'data-tick="elapsed">' + mmss(elapsed / 1000) + '</div>' +
          '<div class="lbl mt-1.5 text-[12px]">Elapsed</div>' +
        '</div>' +
        link(portUrl('Run', run.run_id), 'Open in Port ↗',
          'inline-flex items-center border border-line px-5 py-2.5 text-[15px] text-dim hover:text-ink hover:border-dim') +
      '</div>' +
    '</section>';

    return '<div class="space-y-8">' +
      header +
      renderPipeline(run) +
      renderVerdict(run) +
      renderGate(run) +
      renderEvidence(run) +
    '</div>';
  }

  function liveSig() {
    var r = S.run;
    if (!r) return 'empty:' + S.loaded.run;
    var stages = deriveStages(r);
    return [
      r.run_id, r.stage, r.status, r.attempts, r.classification, r.should_act,
      r.confidence, !!r.verify, !!r.verify_failure, r.changeset.length, r.pr_url,
      STAGES.map(function (n) { return (stages[n] || {}).status + (stages[n] || {}).attempts; }).join(','),
    ].join('|');
  }

  // ═════════════════════════════════════════════════════ SCREEN 2 — FINDINGS

  var SEV_RANK = { HIGH: 3, MED: 2, LOW: 1 };

  function filterBar() {
    var routes = ['all'].concat(Object.keys((S.findings || []).reduce(function (a, f) {
      a[f.route] = 1; return a;
    }, {})).sort());

    function group(name, options, current) {
      return '<div class="flex items-center gap-2">' +
        '<span class="lbl text-[12px]">' + name + '</span>' +
        '<div class="flex border border-line">' +
          options.map(function (o) {
            var on = String(current) === String(o);
            return '<button data-act="filter" data-key="' + name.toLowerCase() + '" data-value="' + esc(o) + '" ' +
              'class="px-3 py-1.5 text-[14px] ' + (on ? 'bg-raise text-ink' : 'text-dim hover:text-ink') +
              ' border-r border-line last:border-r-0' + (on ? ' font-semibold' : '') + '">' +
              (o === 'all' ? 'All' : esc(o)) + '</button>';
          }).join('') +
        '</div>' +
      '</div>';
    }

    return '<div class="flex flex-wrap items-center gap-6">' +
      group('Severity', ['all', 'HIGH', 'MED', 'LOW'], S.filters.severity) +
      group('Status', ['all', 'open', 'fixing', 'suppressed'], S.filters.status) +
      (routes.length > 2 ? group('Route', routes, S.filters.route) : '') +
    '</div>';
  }

  function findingRow(f) {
    var open = !!S.expanded[f.finding_id];
    var st = FSTATUS[f.status] || FSTATUS.open;
    return '<div class="border-t border-line">' +
      '<div class="flex items-center gap-4 px-5 py-3 hover:bg-raise cursor-pointer" ' +
        'data-act="toggle-finding" data-id="' + esc(f.finding_id) + '">' +
        '<span class="shrink-0">' + sevChip(f.severity) + '</span>' +
        '<span class="font-mono text-[14px] text-ink shrink-0 w-12">' + esc(f.check_id) + '</span>' +
        '<span class="text-[16px] text-ink truncate">' + esc(f.title) + '</span>' +
        (f.occurrences > 1
          ? '<span class="shrink-0 border border-line px-1.5 py-0.5 font-mono text-[12px] text-dim" ' +
            'title="seen in ' + f.occurrences + ' audits">×' + f.occurrences + '</span>'
          : '') +
        '<span class="flex-1 truncate text-[14px] text-mute font-mono hidden xl:inline">' + esc(f.evidence) + '</span>' +
        '<span class="shrink-0">' + chip(st.label, st.c, st.glyph) + '</span>' +
        '<span class="shrink-0 text-[12px] text-mute font-mono w-4 text-center">' + (open ? '−' : '+') + '</span>' +
      '</div>' +
      (open
        ? '<div class="px-5 pb-5 pt-1 bg-bg">' +
            '<div class="lbl text-[12px] mb-2">Evidence</div>' +
            '<pre class="border border-line bg-surface px-4 py-3 font-mono text-[14px] leading-relaxed ' +
              'text-dim whitespace-pre-wrap break-words">' + esc(f.evidence || '(none recorded)') + '</pre>' +
            (f.hint
              ? '<div class="mt-4"><div class="lbl text-[12px] mb-2">Suggested fix</div>' +
                '<p class="text-[15px] text-dim">' + esc(f.hint) + '</p></div>'
              : '') +
            '<div class="mt-4 flex flex-wrap items-center gap-5 text-[14px] text-mute">' +
              '<span>first seen <span class="font-mono text-dim">' + rel(f.first_seen) + '</span></span>' +
              (f.finding_id ? '<span class="font-mono">' + esc(f.finding_id) + '</span>' : '') +
              (f.run_id
                ? '<span>last run ' + link(portUrl('Run', f.run_id), f.run_id,
                    'font-mono text-dim hover:text-ink underline underline-offset-4') + '</span>'
                : '') +
              link(portUrl('Finding', f.finding_id), 'Open in Port ↗',
                'ml-auto text-dim hover:text-ink underline underline-offset-4') +
            '</div>' +
          '</div>'
        : '') +
      (f.status === 'suppressed' && f.justification
        ? '<div class="px-5 pb-3 -mt-1"><p class="text-[14px] italic text-mute max-w-[900px] leading-relaxed">' +
          '⊘ ' + esc(f.justification) + '</p></div>'
        : '') +
    '</div>';
  }

  function renderFindings() {
    if (!S.loaded.findings) return skeleton(6);
    var all = S.findings || [];

    var rows = all.filter(function (f) {
      if (S.filters.severity !== 'all' && f.severity !== S.filters.severity) return false;
      if (S.filters.status !== 'all' && f.status !== S.filters.status) return false;
      if (S.filters.route !== 'all' && f.route !== S.filters.route) return false;
      return true;
    });

    if (!all.length) {
      return '<div class="space-y-6">' + filterBar() +
        emptyState('No open findings',
          'Every audited route is clean. The next audit runs in ' +
            '<span class="font-mono text-ink" data-tick="countdown">' + mmss(S.countdown) + '</span>.',
          'Run audit now', 'audit-now') + '</div>';
    }

    var byRoute = {};
    rows.forEach(function (f) { (byRoute[f.route] = byRoute[f.route] || []).push(f); });

    var routes = Object.keys(byRoute).sort(function (a, b) {
      var wa = Math.max.apply(null, byRoute[a].map(function (f) { return SEV_RANK[f.severity] || 0; }));
      var wb = Math.max.apply(null, byRoute[b].map(function (f) { return SEV_RANK[f.severity] || 0; }));
      if (wa !== wb) return wb - wa;
      return byRoute[b].length - byRoute[a].length;
    });

    var grades = (S.status && S.status.grades) || {};

    var groups = routes.map(function (route) {
      var list = byRoute[route].slice().sort(function (a, b) {
        return (SEV_RANK[b.severity] || 0) - (SEV_RANK[a.severity] || 0);
      });
      var counts = list.reduce(function (a, f) {
        if (f.status !== 'suppressed') a[f.severity] = (a[f.severity] || 0) + 1;
        return a;
      }, {});
      var countText = ['HIGH', 'MED', 'LOW'].filter(function (s) { return counts[s]; })
        .map(function (s) {
          return '<span style="color:' + SEV[s].c + '">' + counts[s] + ' ' + s + '</span>';
        }).join('<span class="text-mute mx-2">·</span>') || '<span class="text-mute">none open</span>';

      return '<section class="border border-line bg-surface">' +
        '<div class="flex items-center gap-4 px-5 py-4 flex-wrap">' +
          '<span class="font-mono text-[17px] text-ink">' + esc(route) + '</span>' +
          gradeChip(grades[route] || 'gold') +
          '<span class="text-[14px]">' + countText + '</span>' +
          '<span class="ml-auto text-[14px] text-mute">last audit ' +
            '<span class="font-mono text-dim">' + rel(mostRecentAudit(route)) + '</span></span>' +
        '</div>' +
        list.map(findingRow).join('') +
      '</section>';
    }).join('');

    if (!routes.length) {
      groups = emptyState('No findings match these filters',
        'Widen the filters to see the other ' + all.length + ' finding' + (all.length === 1 ? '' : 's') + '.',
        'Clear filters', 'clear-filters');
    }

    return '<div class="space-y-6">' + filterBar() + groups + '</div>';
  }

  function mostRecentAudit(route) {
    var page = (S.catalog || []).filter(function (p) { return p.route === route; })[0];
    return page ? page.last_audit : null;
  }

  function findingsSig() {
    // The catalog supplies each route's last-audit time, so it belongs in the
    // signature — otherwise the header reads "—" until a finding itself changes.
    return JSON.stringify([S.loaded.findings, S.filters, S.expanded,
      (S.findings || []).map(function (f) {
        return [f.finding_id, f.status, f.severity, f.occurrences].join(':');
      }),
      (S.catalog || []).map(function (p) { return p.route + ':' + p.last_audit; })]);
  }

  // ═════════════════════════════════════════════════════════ SCREEN 3 — RUNS

  function sparkline(values) {
    if (!values || !values.length) return '';
    var w = 240, h = 40, gap = 2;
    var max = Math.max.apply(null, values) || 1;
    var bw = (w - gap * (values.length - 1)) / values.length;
    var bars = values.map(function (v, i) {
      var bh = Math.max(2, (v / max) * h);
      return '<rect x="' + (i * (bw + gap)).toFixed(1) + '" y="' + (h - bh).toFixed(1) + '" ' +
        'width="' + bw.toFixed(1) + '" height="' + bh.toFixed(1) + '" fill="' +
        (i === values.length - 1 ? '#FB923C' : '#334155') + '"/>';
    }).join('');
    return '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" ' +
      'role="img" aria-label="Runs per hour, last ' + values.length + ' hours">' + bars + '</svg>';
  }

  function runTimeline(run) {
    var stages = run.stages || {};
    var present = STAGES.filter(function (n) { return stages[n]; });
    if (!present.length) {
      return '<p class="text-[14px] text-mute">No per-stage timing was recorded for this run.</p>';
    }
    var total = present.reduce(function (a, n) { return a + (stages[n].duration_ms || 0); }, 0) || 1;
    return '<div class="space-y-1.5">' + present.map(function (n) {
      var st = stages[n];
      var skipped = st.status === 'skipped';
      var pct = skipped ? 0 : ((st.duration_ms || 0) / total) * 100;
      return '<div class="flex items-center gap-3">' +
        '<span class="lbl text-[12px] w-20 shrink-0 ' + (skipped ? 'line-through opacity-50' : '') + '">' + n + '</span>' +
        '<div class="flex-1 h-2.5 bg-bg border border-line">' +
          (skipped ? '' : '<div class="h-full" style="width:' + Math.max(pct, 0.6) + '%;background:' +
            (st.status === 'done' ? '#34D399' : '#60A5FA') + '"></div>') +
        '</div>' +
        '<span class="font-mono text-[13px] w-20 text-right ' + (skipped ? 'text-mute' : 'text-dim') + '">' +
          (skipped ? 'skipped' : fmtMs(st.duration_ms)) + '</span>' +
        (st.attempts > 1
          ? '<span class="font-mono text-[12px] w-8" style="color:#F59E0B">×' + st.attempts + '</span>'
          : '<span class="w-8"></span>') +
      '</div>';
    }).join('') + '</div>';
  }

  function renderRuns() {
    if (!S.loaded.runs) return skeleton(8);
    var runs = (S.runs || []).slice(0, 20);

    if (!runs.length) {
      return emptyState('No runs yet',
        'The factory has not opened a run. Submit a brief, or start an audit and let it find its own work.',
        'Run audit now', 'audit-now');
    }

    var totals = runs.reduce(function (a, r) {
      var key = r.outcome || 'in flight';
      a[key] = (a[key] || 0) + 1;
      return a;
    }, {});
    var totalChips = Object.keys(totals).sort(function (a, b) { return totals[b] - totals[a]; })
      .map(function (k) {
        var o = OUTCOMES[k] || { c: '#5C5C64', label: k };
        return chip(o.label + ' ' + totals[k], o.c, null, 'text-[14px]');
      }).join('');

    var head = '<section class="flex items-end justify-between gap-8 flex-wrap">' +
      '<div><div class="lbl mb-2">Runs per hour · last 12h</div>' + sparkline(S.status && S.status.runs_per_hour) + '</div>' +
      '<div class="flex-1 min-w-[300px]"><div class="lbl mb-2">Outcomes · last ' + runs.length + ' runs</div>' +
        '<div class="flex flex-wrap gap-2">' + totalChips + '</div></div>' +
    '</section>';

    var rows = runs.map(function (r) {
      var open = !!S.expanded[r.run_id];
      var o = OUTCOMES[r.outcome] || { c: '#60A5FA', label: r.outcome || 'in flight' };
      var cls = CLASSES[r.classification] || { c: '#5C5C64' };
      return '<tr class="border-t border-line hover:bg-raise cursor-pointer" ' +
          'data-act="toggle-run" data-id="' + esc(r.run_id) + '">' +
          '<td class="px-4 py-3 font-mono text-[14px] text-dim whitespace-nowrap">' + rel(r.started_at) + '</td>' +
          '<td class="px-4 py-3">' + intakeBadge(r.intake) + '</td>' +
          '<td class="px-4 py-3 font-mono text-[13px] text-mute whitespace-nowrap">' + esc(r.trigger) + '</td>' +
          '<td class="px-4 py-3 text-[15px] text-ink max-w-[320px] truncate" title="' + esc(r.title) + '">' +
            esc(r.title) + '</td>' +
          '<td class="px-4 py-3">' + (r.classification
            ? chip(r.classification, cls.c, null, 'text-[12px] font-semibold')
            : '<span class="text-mute text-[14px]">—</span>') + '</td>' +
          '<td class="px-4 py-3">' + chip(o.label, o.c, null, 'text-[13px]') + '</td>' +
          '<td class="px-4 py-3 font-mono text-[14px] text-dim whitespace-nowrap">' + fmtMs(r.duration_ms) + '</td>' +
          '<td class="px-4 py-3 whitespace-nowrap">' +
            '<div class="flex gap-1.5">' +
              link(r.pr_url, 'PR', miniLink(!!r.pr_url)) +
              link(traceUrl(r.trace_id), 'TRACE', miniLink(!!r.trace_id)) +
              link(portUrl('Run', r.run_id), 'PORT', miniLink(true)) +
            '</div>' +
          '</td>' +
          '<td class="px-2 py-3 text-[12px] text-mute font-mono">' + (open ? '−' : '+') + '</td>' +
        '</tr>' +
        (open
          ? '<tr class="bg-bg"><td colspan="9" class="px-4 py-5">' +
              '<div class="flex gap-10 flex-wrap">' +
                '<div class="flex-1 min-w-[420px]">' +
                  '<div class="lbl text-[12px] mb-3">Stage timeline</div>' + runTimeline(r) +
                '</div>' +
                '<div class="w-[280px]">' +
                  '<div class="lbl text-[12px] mb-3">Run</div>' +
                  '<div class="space-y-1.5 text-[14px]">' +
                    kv('run id', '<span class="font-mono text-dim">' + esc(r.run_id) + '</span>') +
                    kv('attempts', '<span class="font-mono text-dim">' + (r.attempts + 1) + '</span>') +
                    kv('trace', r.trace_id
                      ? '<span class="font-mono text-dim truncate inline-block max-w-[170px] align-bottom" title="' +
                        esc(r.trace_id) + '">' + esc(r.trace_id) + '</span>'
                      : '<span class="text-mute">—</span>') +
                    kv('started', '<span class="font-mono text-dim">' +
                      (r.started_at ? new Date(r.started_at).toLocaleTimeString() : '—') + '</span>') +
                  '</div>' +
                '</div>' +
              '</div>' +
            '</td></tr>'
          : '');
    }).join('');

    var table = '<section class="border border-line bg-surface overflow-x-auto">' +
      '<table class="w-full text-left border-collapse">' +
        '<thead><tr class="bg-bg">' +
          ['Started', 'Intake', 'Trigger', 'Title', 'Classification', 'Outcome', 'Duration', 'Links', '']
            .map(function (h) { return '<th class="px-4 py-3 lbl text-[12px] font-normal whitespace-nowrap">' + h + '</th>'; })
            .join('') +
        '</tr></thead>' +
        '<tbody>' + rows + '</tbody>' +
      '</table>' +
    '</section>';

    return '<div class="space-y-7">' + head + table + '</div>';
  }

  function miniLink(enabled) {
    return 'inline-flex items-center border px-2 py-1 font-mono text-[12px] tracking-wide ' +
      (enabled ? 'border-line text-dim hover:text-ink hover:border-dim' : 'border-line text-mute');
  }

  function kv(k, v) {
    return '<div class="flex justify-between gap-3"><span class="text-mute">' + esc(k) + '</span>' + v + '</div>';
  }

  function runsSig() {
    return JSON.stringify([S.loaded.runs, S.expanded, (S.runs || []).map(function (r) {
      return [r.run_id, r.outcome, r.duration_ms].join(':');
    }), S.status && S.status.runs_per_hour]);
  }

  // ══════════════════════════════════════════════════════ SCREEN 4 — CATALOG

  function renderCatalog() {
    if (!S.loaded.catalog) return skeleton(4);
    var pages = S.catalog || [];

    if (!pages.length) {
      return emptyState('The catalog is empty',
        'Nothing has been built yet. Submit a brief and the first page the factory ships will appear here, ' +
        'graded and audited on a schedule.',
        'Submit a brief', 'goto-submit');
    }

    var cards = pages.slice().sort(function (a, b) {
      var rank = { bronze: 0, silver: 1, gold: 2 };
      return (rank[a.grade] || 0) - (rank[b.grade] || 0);
    }).map(function (p) {
      var below = p.grade !== 'gold' && p.grade !== 'silver';
      return '<article class="border border-line bg-surface p-6 ' +
          (below ? 'border-l-[6px]' : '') + '" ' + (below ? 'style="border-left-color:#EF4444"' : '') + '>' +
        '<div class="flex items-start justify-between gap-4">' +
          '<div class="min-w-0">' +
            '<div class="font-mono text-[17px] text-ink truncate">' + esc(p.route) + '</div>' +
            '<div class="mt-1 text-[15px] text-dim truncate">' + esc(p.title) + '</div>' +
          '</div>' +
          gradeChip(p.grade, true) +
        '</div>' +

        '<div class="mt-6 flex items-end gap-7">' +
          '<div><div class="text-[26px] leading-none font-semibold tabular-nums" style="color:' +
            (p.high > 0 ? '#EF4444' : '#5C5C64') + '">' + p.high + '</div>' +
            '<div class="lbl mt-1 text-[12px]">Open high</div></div>' +
          '<div><div class="text-[26px] leading-none font-semibold tabular-nums" style="color:' +
            (p.med > 0 ? '#F59E0B' : '#5C5C64') + '">' + p.med + '</div>' +
            '<div class="lbl mt-1 text-[12px]">Open med</div></div>' +
        '</div>' +

        '<div class="mt-6 pt-4 border-t border-line space-y-1.5 text-[14px]">' +
          kv('last audit', '<span class="font-mono text-dim">' + rel(p.last_audit) + '</span>') +
          kv('built by', p.created_by_run
            ? link(portUrl('Run', p.created_by_run), p.created_by_run,
                'font-mono text-dim hover:text-ink underline underline-offset-4')
            : '<span class="text-mute">—</span>') +
        '</div>' +

        '<div class="mt-5">' +
          link(portUrl('Page', p.page_id), 'Open in Port ↗',
            'inline-flex items-center border border-line px-4 py-2 text-[14px] text-dim hover:text-ink hover:border-dim') +
        '</div>' +
      '</article>';
    }).join('');

    return '<div class="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6">' + cards + '</div>';
  }

  function catalogSig() {
    return JSON.stringify([S.loaded.catalog, (S.catalog || []).map(function (p) {
      return [p.route, p.grade, p.high, p.med, p.last_audit].join(':');
    })]);
  }

  // ═══════════════════════════════════════════════════════ SCREEN 5 — SUBMIT

  function renderSubmit() {
    return '<div class="max-w-[820px]">' +
      '<h1 class="text-[30px] font-semibold text-ink">Submit a brief</h1>' +
      '<p class="mt-2 text-[16px] text-dim leading-relaxed">' +
        'Describe the feature. Triage decides whether it is coherent and in scope, then the factory ' +
        'plans it, writes it, tests it, re-audits it, and holds it at the gate for a human.' +
      '</p>' +

      '<form id="brief-form" class="mt-8 space-y-6" novalidate>' +
        '<div>' +
          '<label for="brief-title" class="lbl block mb-2">Title</label>' +
          '<input id="brief-title" type="text" maxlength="120" ' +
            'placeholder="Add a pricing page" ' +
            'class="w-full bg-surface border border-line px-4 py-3 text-[16px] text-ink ' +
            'placeholder:text-mute focus:border-info focus:outline-none">' +
          '<p class="mt-1.5 text-[13px] text-mute">Optional. Left blank, the first line of the description is used.</p>' +
        '</div>' +

        '<div>' +
          '<label class="lbl block mb-2">Description</label>' +
          '<div id="brief-composer"></div>' +
          '<p class="mt-2 text-[13px] text-mute">' +
            'Enter submits, Shift+Enter starts a new line. The bars set priority.' +
            (window.ForgeChatInput && window.ForgeChatInput.dictationAvailable
              ? ' The microphone dictates.' : '') +
          '</p>' +
        '</div>' +

        '<div id="brief-msg" class="min-h-[24px] text-[15px]"></div>' +
      '</form>' +

      '<div class="mt-8">' +
        '<div class="lbl mb-3">Start from an example</div>' +
        '<div class="flex flex-wrap gap-2">' +
          EXAMPLE_BRIEFS.map(function (b, i) {
            var short = b.split('.')[0];
            return '<button data-act="example" data-i="' + i + '" ' +
              'class="border border-line bg-surface px-3.5 py-2 text-left text-[14px] text-dim ' +
              'hover:text-ink hover:border-dim max-w-[380px] truncate">' + esc(short) + '</button>';
          }).join('') +
        '</div>' +
      '</div>' +

      '<p class="mt-8 pt-5 border-t border-line text-[14px] text-mute">' +
        'Submissions are recorded as change requests in Port.' +
      '</p>' +
    '</div>';
  }

  function mountComposer() {
    var host = document.getElementById('brief-composer');
    if (!host || !window.ForgeChatInput) return;
    S.composer = window.ForgeChatInput.mount(host, {
      placeholder: 'Describe the feature to build…',
      onSubmit: submitBrief,
    });
  }

  function briefMessage(text, tone) {
    var el = document.getElementById('brief-msg');
    if (!el) return;
    var colors = { ok: 'text-ok', bad: 'text-bad', busy: 'text-dim' };
    el.className = 'min-h-[24px] text-[15px] ' + (colors[tone] || 'text-dim');
    el.textContent = text;
  }

  function submitBrief(text, meta) {
    if (!text) return;
    var titleEl = document.getElementById('brief-title');
    var payload = {
      title: (titleEl && titleEl.value.trim()) || text.split('\n')[0].slice(0, 80),
      description: text,
      priority: meta.priority,
    };

    if (S.demo) {
      briefMessage('Not submitted: forge-control is not reachable, so the console is on the offline dataset. ' +
        'The brief was not sent anywhere.', 'bad');
      return;
    }

    S.composer.setBusy(true, 'submitting…');
    briefMessage('Submitting to Port…', 'busy');

    request('brief', '/api/brief', { method: 'POST', body: payload })
      .then(function (res) {
        S.composer.setBusy(false);
        if (res === undefined) { briefMessage('A submission is already in flight.', 'busy'); return; }
        S.composer.clear();
        if (titleEl) titleEl.value = '';
        briefMessage('Submitted. Following the run on Live.', 'ok');
        pollCurrent();
        setTimeout(function () { location.hash = '#live'; }, 450);
      })
      .catch(function (err) {
        S.composer.setBusy(false);
        briefMessage('Could not submit: ' + friendlyError(err) + ' The brief is still in the box — nothing was lost.', 'bad');
      });
  }

  function friendlyError(err) {
    var m = String((err && err.message) || err || '');
    if (/aborted|abort/i.test(m)) return 'forge-control did not answer in time.';
    if (/HTTP 4\d\d/.test(m)) return 'forge-control rejected the request (' + m + ').';
    if (/HTTP 5\d\d/.test(m)) return 'forge-control returned an error (' + m + ').';
    return 'forge-control is not reachable.';
  }

  // ══════════════════════════════════════════════════════════════ rendering

  var RENDERERS = {
    live:     { html: renderLive,     sig: liveSig },
    findings: { html: renderFindings, sig: findingsSig },
    runs:     { html: renderRuns,     sig: runsSig },
    catalog:  { html: renderCatalog,  sig: catalogSig },
    submit:   { html: renderSubmit,   sig: function () { return 'submit'; } },
  };

  function renderScreen(force) {
    var r = RENDERERS[S.screen] || RENDERERS.live;
    var sig = S.screen + '::' + r.sig();
    if (!force && S.sigs.screen === sig) return;   // no flicker on an unchanged poll
    S.sigs.screen = sig;
    document.getElementById('screen').innerHTML = r.html();
    if (S.screen === 'submit') mountComposer();
    tick();
  }

  /** Clocks. These write text into existing nodes; they never re-render. */
  function tick() {
    var cd = mmss(S.countdown);
    document.querySelectorAll('[data-tick="countdown"]').forEach(function (n) { n.textContent = cd; });

    if (S.run && S.runStart) {
      var el = document.querySelector('[data-tick="elapsed"]');
      if (el) el.textContent = mmss((Date.now() - S.runStart) / 1000);

      var stages = deriveStages(S.run);
      var active = STAGES.filter(function (n) { return (stages[n] || {}).status === 'active'; })[0];
      var dur = document.querySelector('[data-tick="stage-dur"]');
      if (dur && active && stages[active].duration_ms != null) {
        dur.textContent = fmtMs(stages[active].duration_ms);
      }
    }
  }

  // ═══════════════════════════════════════════════════════════════ actions

  function runAuditNow() {
    if (S.demo) {
      flashToast('forge-control is not reachable — nothing was triggered.');
      return;
    }
    request('audit', '/audit/run', { method: 'POST', body: {} })
      .then(function () { flashToast('Audit requested.'); pollStatus(); pollCurrent(); })
      .catch(function (err) { flashToast('Could not start an audit: ' + friendlyError(err)); });
  }

  function flashToast(text) {
    if (S.toast) clearTimeout(S.toast);
    var el = document.getElementById('forge-toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'forge-toast';
      el.className = 'fixed bottom-6 left-1/2 -translate-x-1/2 z-40 border border-line bg-surface ' +
        'px-5 py-3 text-[15px] text-ink shadow-lg';
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.hidden = false;
    S.toast = setTimeout(function () { el.hidden = true; }, 4000);
  }

  document.addEventListener('click', function (e) {
    var pop = document.querySelector('[data-user-pop]');
    if (pop && !pop.hidden && !e.target.closest('#user-menu')) pop.hidden = true;
    if (e.target.closest('a')) return;   // a link inside a clickable row stays a link
    var t = e.target.closest('[data-act]');
    if (!t) return;
    var act = t.getAttribute('data-act');

    if (act === 'refresh') { refreshAll(); renderScreen(true); flashToast('Refreshed.'); }
    else if (act === 'user-toggle') {
      var pop = document.querySelector('[data-user-pop]');
      if (pop) pop.hidden = !pop.hidden;
    }
    else if (act === 'audit-now') { runAuditNow(); }
    else if (act === 'goto-submit') { location.hash = '#submit'; }
    else if (act === 'signout') { window.ForgeAuth.signOut(); }
    else if (act === 'filter') {
      S.filters[t.getAttribute('data-key')] = t.getAttribute('data-value');
      renderScreen();
    }
    else if (act === 'clear-filters') {
      S.filters = { severity: 'all', route: 'all', status: 'all' };
      renderScreen();
    }
    else if (act === 'toggle-finding' || act === 'toggle-run') {
      var id = t.getAttribute('data-id');
      if (S.expanded[id]) delete S.expanded[id]; else S.expanded[id] = true;
      renderScreen();
    }
    else if (act === 'example') {
      var text = EXAMPLE_BRIEFS[+t.getAttribute('data-i')];
      if (S.composer) S.composer.setValue(text);
    }
  });

  document.addEventListener('keydown', function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    var tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || e.target.isContentEditable) return;

    if (e.key >= '1' && e.key <= '5') {
      location.hash = '#' + SCREENS[+e.key - 1];
    } else if (e.key === 'r' || e.key === 'R') {
      refreshAll();
      renderScreen(true);
      flashToast('Refreshed.');
    }
  });

  // ════════════════════════════════════════════════════════════════ routing

  function route() {
    var want = (location.hash || '#live').slice(1).toLowerCase();
    S.screen = SCREENS.indexOf(want) >= 0 ? want : 'live';
    if (S.composer && S.screen !== 'submit') { S.composer = null; }
    renderRail();
    renderScreen(true);
    if (S.screen === 'runs') pollRuns();
    if (S.screen === 'catalog') pollCatalog();
    if (S.screen === 'findings') pollFindings();
  }

  window.addEventListener('hashchange', route);

  // ═══════════════════════════════════════════════════════════════════ boot

  function boot() {
    document.getElementById('app').hidden = false;
    renderRail();
    renderStatusBar();
    route();

    pollStatus();
    pollCurrent();
    pollFindings();
    pollRuns();
    pollCatalog();

    setInterval(pollCurrent, CFG.pollCurrentMs || 1000);
    setInterval(function () { if (S.demo) S.status = normStatus(window.FORGE_DEMO.status()); pollStatus(); },
      CFG.pollStatusMs || 3000);
    setInterval(pollFindings, CFG.pollFindingsMs || 5000);
    setInterval(function () { if (S.screen === 'runs') pollRuns(); }, CFG.pollRunsMs || 5000);
    setInterval(function () { if (S.screen === 'catalog') pollCatalog(); }, CFG.pollCatalogMs || 10000);

    setInterval(function () {
      if (S.countdown != null && S.countdown > 0) S.countdown -= 1;
      tick();
    }, 1000);
  }

  window.ForgeAuth.init().then(function (r) {
    if (r.enabled) {
      window.ForgeAuth.onChange(function (session) {
        if (!session) location.reload();
        else renderRail();
      });
    }
    boot();
  });
})();
