/*
 * forge/console/demo-data.js — the offline dataset.
 *
 * forge-control may not be up. When the API cannot be reached on first load the
 * console falls back to this, and says so: the status bar carries a DEMO DATA
 * pill for as long as it is being used. The console never presents invented
 * numbers as live ones.
 *
 * The live run here is scripted against a clock, so the pipeline actually walks
 * and the screen is worth pointing a camera at with nothing else running. The
 * script deliberately covers every state the Live screen can render: a verify
 * failure and retry, a gate hold, and a run that declines to write any code.
 */
(function () {
  'use strict';

  var T0 = Date.now();
  var CYCLE = 168; // seconds: two runs and a quiet gap, then repeat

  var STAGE_ORDER = ['INTAKE', 'CONTEXT', 'TRIAGE', 'PLAN', 'ACT', 'VERIFY', 'GATE', 'RELEASE'];

  // ---------------------------------------------------------------- findings
  var FINDINGS = [
    {
      finding_id: 'f_3a1c', check_id: 'S1', severity: 'HIGH', route: '/', status: 'fixing',
      title: 'Content-Security-Policy present', category: 'security', occurrences: 4,
      first_seen: iso(-3600 * 26), run_id: 'run_9f2c1ab4',
      evidence: 'No Content-Security-Policy header, observed on 200 response for /',
      suggested_fix_hint: 'Add security-headers middleware setting Content-Security-Policy',
    },
    {
      finding_id: 'f_77b2', check_id: 'S2', severity: 'HIGH', route: '/', status: 'open',
      title: 'X-Frame-Options or CSP frame-ancestors present', category: 'security', occurrences: 4,
      first_seen: iso(-3600 * 26), run_id: null,
      evidence: 'Neither X-Frame-Options nor CSP frame-ancestors present, observed on 200 response for /',
      suggested_fix_hint: 'Set X-Frame-Options DENY or CSP frame-ancestors none',
    },
    {
      finding_id: 'f_1d09', check_id: 'Q1', severity: 'LOW', route: '/', status: 'open',
      title: 'Images have alt text', category: 'quality', occurrences: 1,
      first_seen: iso(-3600 * 5), run_id: null,
      evidence: '3 of 7 img elements have no alt text: hero.svg, mark.svg, grid.svg',
      suggested_fix_hint: 'Add alt attributes; empty alt="" for decorative images',
    },
    {
      finding_id: 'f_5e44', check_id: 'P1', severity: 'MED', route: '/products', status: 'suppressed',
      title: 'Response under threshold', category: 'performance', occurrences: 9,
      first_seen: iso(-3600 * 48), run_id: 'run_41d0e7b9',
      evidence: '742ms server response, threshold 500ms, observed on 200 response for /products',
      suggested_fix_hint: 'Cache the scraped catalog; it is recomputed per request',
      justification: 'Real, but the only fix that reaches 500ms is caching the Bright Data scrape, and catalog freshness is a decision a human owns. Opened as an issue with the analysis; no code written.',
    },
    {
      finding_id: 'f_9c31', check_id: 'Q3', severity: 'MED', route: '/products', status: 'open',
      title: 'Page has a title and meta description', category: 'quality', occurrences: 2,
      first_seen: iso(-3600 * 9), run_id: null,
      evidence: 'Page is missing meta description',
      suggested_fix_hint: 'Add a meta description to the Pulse catalog template',
    },
    {
      finding_id: 'f_2b8e', check_id: 'S10', severity: 'LOW', route: '/products', status: 'suppressed',
      title: 'No credential-shaped strings in the body', category: 'security', occurrences: 1,
      first_seen: iso(-3600 * 31), run_id: 'run_c07a55e1',
      evidence: 'base64-shaped string of length 71 near "<script integrity=" — value redacted (sha3...c19f)',
      suggested_fix_hint: 'Confirm the match is not a live credential',
      justification: 'The matched string is a Subresource Integrity hash on a script tag, not a credential. SRI hashes are base64 digests and are meant to be public. Suppressed, with this reason recorded in the catalog.',
    },
    {
      finding_id: 'f_6fa0', check_id: 'Q2', severity: 'LOW', route: '/', status: 'open',
      title: 'External links carry rel=noopener', category: 'quality', occurrences: 1,
      first_seen: iso(-3600 * 2), run_id: null,
      evidence: '2 external link(s) without rel=noopener: brightdata.com, github.com/k1lst1x/FORGE',
      suggested_fix_hint: 'Add rel="noopener noreferrer" to target=_blank links',
    },
  ];

  // ----------------------------------------------------------------- catalog
  var CATALOG = [
    {
      route: '/', title: 'Pulse — home', grade: 'bronze', high: 2, med: 0, low: 1,
      last_audit: iso(-47), created_by_run: 'run_0a11c3d2', page_id: 'pulse_home',
    },
    {
      route: '/products', title: 'Pulse — scraped catalog', grade: 'silver', high: 0, med: 1, low: 0,
      last_audit: iso(-47), created_by_run: 'run_41d0e7b9', page_id: 'pulse_products',
    },
  ];

  // -------------------------------------------------------------- run history
  var HISTORY = [
    hrun('run_9f2c1ab4', -1140, 'finding', 'scheduler', 'S1 on /', 'AUTOFIX_SAFE', 'merged', 84200, 1),
    hrun('run_41d0e7b9', -3300, 'finding', 'scheduler', 'P1 on /products', 'NEEDS_HUMAN_DESIGN', 'escalated', 11400, 0),
    hrun('run_c07a55e1', -5220, 'finding', 'signoz alert', 'S10 on /products', 'FALSE_POSITIVE', 'suppressed', 9100, 0),
    hrun('run_0a11c3d2', -7800, 'brief', 'port brief', 'Show the five cheapest products', 'NEW_FEATURE', 'merged', 196300, 1),
    hrun('run_5512bd8f', -9600, 'finding', 'scheduler', 'S4 on /products', 'AUTOFIX_SAFE', 'merged', 71900, 0),
    hrun('run_bb31c0a7', -11400, 'finding', 'scheduler', 'S2 on /', 'DUPLICATE', 'attached_to_existing_run', 4300, 0),
    hrun('run_88be14aa', -14700, 'brief', 'port brief', 'Expose JSON products API', 'NEW_FEATURE', 'merged', 174000, 0),
    hrun('run_e7710b23', -18000, 'finding', 'scheduler', 'S11 on /', 'AUTOFIX_SAFE', 'merged', 66700, 1),
    hrun('run_1c9de440', -21600, 'finding', 'scheduler', 'S1 on /products', 'AUTOFIX_SAFE', 'rejected_by_human', 90400, 0),
    hrun('run_a4f0e918', -25200, 'finding', 'scheduler', 'Q4 on /products', 'AUTOFIX_SAFE', 'merged', 58200, 0),
    hrun('run_37c1ba05', -28800, 'finding', 'watchdog', 'S1 on /', 'UPSTREAM_OUTAGE', 'escalated', 6800, 0),
    hrun('run_d90b6c17', -32400, 'brief', 'port brief', 'Group out-of-stock items by price', 'NEW_FEATURE', 'merged', 210500, 2),
  ];

  // ------------------------------------------------------------- the live run
  // Each script entry is [stage, seconds spent in it].
  var RUN_A = {
    run_id: 'run_b41f7e0c', intake: 'finding', trigger: 'scheduler',
    title: 'S1 on / — Content-Security-Policy missing',
    check_id: 'S1', severity: 'HIGH', route: '/', finding_id: 'f_3a1c',
    evidence: 'No Content-Security-Policy header, observed on 200 response for /',
    trace_id: '4bf92f3577b34da6a3ce929d0e0e4736',
    classification: 'AUTOFIX_SAFE', should_act: true, confidence: 0.88,
    decided_by: 'model', blast_radius: 'contained',
    model: 'claude-sonnet-4-6', tokens_in: 3412, tokens_out: 218,
    justification: 'S1 is contained to pulse/routes/root.py, the single file that serves /. Adding a Content-Security-Policy header changes no response body and no route contract, and the app serves no third-party scripts that a default-src policy would break.',
    changeset: [
      { path: 'pulse/middleware/headers.py', added: 31, removed: 0 },
      { path: 'pulse/routes/root.py', added: 14, removed: 2 },
      { path: 'tests/test_headers.py', added: 22, removed: 0 },
    ],
    verify: {
      tests_passed: 12, tests_total: 12, closed: 3, introduced: 0,
      evidence: 'pytest green (12/12); re-audit of / shows S1 closed and no new findings.',
    },
    pr_url: 'https://github.com/k1lst1x/FORGE/pull/318',
    script: [
      ['INTAKE', 4], ['CONTEXT', 6], ['TRIAGE', 7], ['PLAN', 6], ['ACT', 9],
      ['VERIFY', 7], ['PLAN', 5], ['ACT', 8], ['VERIFY', 7], ['GATE', 16], ['RELEASE', 6],
    ],
    retry_at: 5, // VERIFY fails the first time through
    verify_failure: 'tests/test_headers.py::test_csp FAILED — assert "content-security-policy" in response.headers',
  };

  var RUN_B = {
    run_id: 'run_2ea90d55', intake: 'finding', trigger: 'scheduler',
    title: 'P1 on /products — response over threshold',
    check_id: 'P1', severity: 'MED', route: '/products', finding_id: 'f_5e44',
    evidence: '742ms server response, threshold 500ms, observed on 200 response for /products',
    trace_id: '7d3a1e88b0c24f5aa1de44e02b7c9931',
    classification: 'NEEDS_HUMAN_DESIGN', should_act: false, confidence: 0.79,
    decided_by: 'model', blast_radius: 'clients',
    model: 'claude-sonnet-4-6', tokens_in: 3980, tokens_out: 244,
    justification: 'P1 on /products is real, but the only fix that reaches 500ms is caching the Bright Data scrape, and catalog freshness is a decision a human owns rather than an implementation detail. Opening an issue with the analysis and writing no code.',
    changeset: [], verify: null, pr_url: null,
    issue_url: 'https://github.com/k1lst1x/FORGE/issues/122',
    script: [['INTAKE', 4], ['CONTEXT', 6], ['TRIAGE', 8], ['ESCALATED', 14]],
    retry_at: -1,
  };

  // RUN_A runs 0..81s, quiet until 96, RUN_B 96..128, quiet until 168.
  var SCHEDULE = [{ at: 0, run: RUN_A }, { at: 96, run: RUN_B }];

  // ------------------------------------------------------------------ helpers
  function iso(secondsAgo) {
    return new Date(Date.now() + secondsAgo * 1000).toISOString();
  }

  function hrun(id, ago, intake, trigger, title, cls, outcome, ms, attempts) {
    return {
      run_id: id, started_at: iso(ago), intake: intake, trigger: trigger, title: title,
      classification: cls, outcome: outcome, duration_ms: ms, attempts: attempts,
      trace_id: id.replace('run_', '') + 'a3ce929d0e0e4736',
      pr_url: (outcome === 'merged' || outcome === 'rejected_by_human')
        ? 'https://github.com/forge/pulse/pull/' + (280 + (id.charCodeAt(5) % 40))
        : null,
      stages: stagesFor(cls, ms, attempts),
    };
  }

  function stagesFor(cls, ms, attempts) {
    var acted = cls === 'AUTOFIX_SAFE' || cls === 'NEW_FEATURE';
    var weights = { INTAKE: 2, CONTEXT: 9, TRIAGE: 14, PLAN: 21, ACT: 28, VERIFY: 16, GATE: 6, RELEASE: 4 };
    var names = acted ? STAGE_ORDER : ['INTAKE', 'CONTEXT', 'TRIAGE'];
    var total = names.reduce(function (a, n) { return a + weights[n]; }, 0);
    var out = {};
    STAGE_ORDER.forEach(function (n) { out[n] = { status: 'skipped' }; });
    names.forEach(function (n) {
      out[n] = { status: 'done', duration_ms: Math.round(ms * weights[n] / total) };
      if (attempts && (n === 'PLAN' || n === 'ACT' || n === 'VERIFY')) out[n].attempts = attempts + 1;
    });
    return out;
  }

  function elapsed() {
    return ((Date.now() - T0) / 1000) % CYCLE;
  }

  /** Build the run object the Live screen expects, for the current clock. */
  function currentRun() {
    var t = elapsed();
    var slot = null;
    for (var i = SCHEDULE.length - 1; i >= 0; i--) {
      if (t >= SCHEDULE[i].at) { slot = SCHEDULE[i]; break; }
    }
    if (!slot) return null;

    var spec = slot.run;
    var into = t - slot.at;
    var acc = 0, idx = -1, stageStart = 0;
    for (var j = 0; j < spec.script.length; j++) {
      if (into < acc + spec.script[j][1]) { idx = j; stageStart = acc; break; }
      acc += spec.script[j][1];
    }
    if (idx === -1) return null; // this run has finished; the gap before the next

    var stages = {};
    STAGE_ORDER.forEach(function (n) { stages[n] = { status: 'pending' }; });

    for (var k = 0; k <= idx; k++) {
      var name = spec.script[k][0];
      if (name === 'ESCALATED') continue;
      stages[name] = (k === idx)
        ? { status: 'active', duration_ms: Math.round((into - stageStart) * 1000) }
        : { status: 'done', duration_ms: spec.script[k][1] * 1000 };
    }

    var retried = spec.retry_at >= 0 && idx > spec.retry_at;
    var attempts = retried ? 1 : 0;
    if (retried) {
      ['PLAN', 'ACT', 'VERIFY'].forEach(function (n) {
        if (stages[n].status !== 'pending') stages[n].attempts = 2;
      });
    }

    var triaged = stages.TRIAGE.status === 'done';
    if (triaged && !spec.should_act) {
      ['PLAN', 'ACT', 'VERIFY', 'GATE', 'RELEASE'].forEach(function (n) {
        stages[n] = { status: 'skipped' };
      });
    }

    var stageNow = spec.script[idx][0];
    var escalated = stageNow === 'ESCALATED';
    var verifyDone = stages.VERIFY.status === 'done' && (spec.retry_at < 0 || retried);
    var actStarted = stages.ACT.status === 'done' || stages.ACT.status === 'active';
    var gateReached = stages.GATE.status === 'active' || stages.GATE.status === 'done';

    return {
      run_id: spec.run_id,
      intake: spec.intake,
      trigger: spec.trigger,
      title: spec.title,
      stage: escalated ? 'ESCALATED' : stageNow,
      status: escalated ? 'escalated' : 'running',
      started_at: new Date(Date.now() - into * 1000).toISOString(),
      trace_id: spec.trace_id,
      attempts: attempts,
      stages: stages,
      classification: triaged ? spec.classification : null,
      should_act: triaged ? spec.should_act : null,
      confidence: triaged ? spec.confidence : null,
      justification: triaged ? spec.justification : null,
      decided_by: triaged ? spec.decided_by : null,
      blast_radius: triaged ? spec.blast_radius : null,
      model: triaged ? spec.model : null,
      tokens_in: triaged ? spec.tokens_in : null,
      tokens_out: triaged ? spec.tokens_out : null,
      finding: spec.check_id ? {
        finding_id: spec.finding_id, check_id: spec.check_id, severity: spec.severity,
        route: spec.route, evidence: spec.evidence,
      } : null,
      changeset: actStarted ? spec.changeset : [],
      verify: verifyDone && spec.verify ? spec.verify : null,
      verify_failure: (spec.retry_at >= 0 && !retried && stages.VERIFY.status === 'done')
        ? spec.verify_failure : null,
      pr_url: gateReached ? spec.pr_url : null,
      issue_url: spec.issue_url || null,
      approval_id: stages.GATE.status === 'active' ? 'appr_7c21f0' : null,
    };
  }

  function status() {
    var open = FINDINGS.filter(function (f) { return f.status !== 'suppressed'; });
    var count = function (sev) {
      return open.filter(function (f) { return f.severity === sev; }).length;
    };
    return {
      scheduler: 'healthy',
      next_audit_seconds: Math.max(0, Math.round(CYCLE - elapsed())),
      audit_interval_seconds: CYCLE,
      runs_today: 27,
      severity: { HIGH: count('HIGH'), MED: count('MED'), LOW: count('LOW') },
      grades: { '/': 'bronze', '/products': 'silver' },
      runs_per_hour: [1, 0, 2, 3, 1, 4, 2, 5, 3, 2, 6, 4],
    };
  }

  window.FORGE_DEMO = {
    currentRun: currentRun,
    status: status,
    findings: function () { return FINDINGS.slice(); },
    runs: function () { return HISTORY.slice(); },
    catalog: function () { return CATALOG.slice(); },
  };
})();
