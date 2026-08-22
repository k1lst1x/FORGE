/*
 * forge/console/config.js — the only file you edit to point the console at things.
 *
 * Everything here can also be overridden per-load with a query string, which is
 * what you want on a demo machine:
 *     /console?api=http://localhost:8000     talk to a forge-control elsewhere
 *     /console?demo=1                        force the offline dataset
 *     /console?noauth=1                      skip the Supabase gate for this load
 */
window.FORGE_CONFIG = {
  // Base URL for forge-control. '' means same origin, which is the case when
  // forge-control serves this directory at GET /console.
  apiBase: '',

  // Supabase Auth. Leave blank and the console runs open — the rail says so.
  // Fill both in and the console gates on a session and sends the access token
  // as Authorization: Bearer <jwt> on every API call.
  supabaseUrl: '',
  supabaseAnonKey: '',

  // Where an entity's canonical record lives. {id} is substituted.
  portRunUrl: 'https://app.getport.io/factory_runEntity?identifier={id}',
  portFindingUrl: 'https://app.getport.io/findingEntity?identifier={id}',
  portPageUrl: 'https://app.getport.io/pageEntity?identifier={id}',
  portApprovalUrl: 'https://app.getport.io/approvals?identifier={id}',
  signozTraceUrl: 'https://forge.signoz.io/trace/{id}',

  // Poll intervals in ms. The spec'd cadence; slow them down if a laptop cooks.
  pollCurrentMs: 1000,
  pollStatusMs: 3000,
  pollFindingsMs: 5000,
  pollRunsMs: 5000,
  pollCatalogMs: 10000,
};
