// Lumida web app (Stage 4.4): plain JavaScript, no build step, served by Lumida itself.
//
// - Talks only to Lumida's own API (/api/v1). The login lives in an HttpOnly cookie that
//   this code can't read; every change also sends the X-Lumida-CSRF header.
// - Every value from the API is put on the page with textContent (the h() helper), never
//   as HTML, so data can't inject markup or scripts.
// - Pages are chosen by the address after "#", e.g. #/s/shop/rules.

const API = "/api/v1";
const main = document.getElementById("main");
let me = null; // who is logged in: {email, role, ...}
let newKey = null; // an API key just made, shown once on "My account"
let about = null; // {version, ai_enabled}

// --- small helpers ---------------------------------------------------------------------

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "class") el.className = value;
    else if (key === "style") el.style.cssText = value; // allowed by the page's security policy
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const enc = encodeURIComponent;
const ROLE_RANK = { viewer: 0, reviewer: 1, admin: 2 };
const can = (role) => me && ROLE_RANK[me.role] >= ROLE_RANK[role];

function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function badge(text, kind = "") {
  return h("span", { class: `badge ${kind}` }, text);
}
const sevBadge = (s) => badge(s, `sev-${s}`);
const stBadge = (s) => badge(s, `st-${s}`);

function scoreClass(score) {
  if (score === null || score === undefined) return "muted";
  if (score >= 90) return "good";
  return score >= 70 ? "ok" : "bad";
}

function scoreView(score) {
  if (score === null || score === undefined) return h("div", { class: "muted small" }, "no score yet");
  return h("div", { class: `score ${scoreClass(score)}`, title: "health score" }, Math.round(score), h("small", {}, " /100"));
}

function toast(message, error = false) {
  const el = h("div", { class: `toast${error ? " toast--error" : ""}` }, message);
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), error ? 7000 : 3500);
}

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function api(path, { method = "GET", body, params } = {}) {
  let url = API + path;
  if (params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== "" && v !== null && v !== undefined) q.set(k, v);
    if ([...q].length) url += `?${q}`;
  }
  const headers = { Accept: "application/json" };
  if (method !== "GET") headers["X-Lumida-CSRF"] = "1";
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(url, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 401 && path !== "/auth/login") {
    me = null;
    location.hash = "#/login";
    throw new ApiError(401, "please log in");
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : response.statusText;
    throw new ApiError(response.status, detail);
  }
  return data;
}

// Run an action from a button: disable it meanwhile, report the outcome.
async function act(button, action, done) {
  if (button) button.disabled = true;
  try {
    const result = await action();
    if (done) toast(typeof done === "function" ? done(result) : done);
    await route();
  } catch (err) {
    if (err.status !== 401) toast(err.message, true);
  } finally {
    if (button) button.disabled = false;
  }
}

function show(...content) {
  main.replaceChildren(...content.flat());
  main.focus({ preventScroll: true });
}

function heading(title, crumb, ...right) {
  return h("div", { class: "head" },
    h("div", {}, crumb ? h("div", { class: "crumb" }, crumb) : null, h("h1", {}, title)),
    h("div", { class: "row" }, ...right));
}

function table(columns, rows, empty = "Nothing here yet.") {
  if (!rows.length) return h("div", { class: "empty" }, empty);
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", {}, c)))),
    h("tbody", {}, rows.map((cells) => h("tr", {}, cells.map((c) => h("td", {}, c)))))));
}

function select(name, options, value, onchange) {
  return h("select", { name, "aria-label": name, onchange },
    options.map(([v, label]) => h("option", { value: v, selected: v === value }, label)));
}

// --- jobs ("run now", connection tests, AI work) ---------------------------------------

const JOB_WORDS = { run: "Run", test_connection: "Connection test", propose_rules: "Rule proposals", explain: "Explanations" };
const ACTIVE = new Set(["queued", "running"]);

// Follow a job until it's done, showing its state in `line`; stops if the page changes.
async function watchJob(job, line, done) {
  const render = (j) => line.replaceChildren(stBadge(j.status), " ", JOB_WORDS[j.kind] || j.kind,
    j.status === "queued" ? " waiting to start…" : j.status === "running" ? " in progress…" : "",
    j.status === "failed" && j.error ? h("span", { class: "bad small" }, ` ${j.error}`) : "");
  render(job);
  while (ACTIVE.has(job.status)) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    if (!line.isConnected) return; // the person moved on
    job = await api(`/jobs/${job.id}`);
    render(job);
  }
  if (done) await done(job);
}

function startJob(sourceName, kind, extra = {}) {
  return api(`/sources/${enc(sourceName)}/jobs`, { method: "POST", body: { kind, ...extra } });
}

// A button that starts a job, and a line that follows it; the page reloads when it ends.
function jobButton(sourceName, kind, label, { primary = false, extra = {}, line = h("span", { class: "small" }) } = {}) {
  const button = h("button", { class: `btn${primary ? " btn--amber" : ""}`, onclick: async () => {
    button.disabled = true;
    try {
      const job = await startJob(sourceName, kind, extra);
      await watchJob(job, line, async (j) => {
        const ok = j.status === "succeeded";
        toast(ok ? `${JOB_WORDS[kind]} finished` : `${JOB_WORDS[kind]} failed: ${j.error || "see Runs"}`, !ok);
        await route();
      });
    } catch (err) {
      if (err.status !== 401) toast(err.message, true);
    } finally {
      button.disabled = false;
    }
  } }, label);
  return [button, line];
}

// --- the frame -------------------------------------------------------------------------

function frame(section) {
  const top = document.getElementById("top");
  top.hidden = !me;
  if (!me) return;
  const links = [["#/", "Databases", "sources"], ["#/me", "My account", "me"]];
  if (can("admin")) links.splice(1, 0, ["#/people", "People", "people"], ["#/audit", "Audit log", "audit"]);
  document.getElementById("nav").replaceChildren(
    ...links.map(([href, text, key]) => h("a", { href, class: key === section ? "on" : "" }, text)));
  document.getElementById("who").replaceChildren(
    h("span", {}, me.email, " · ", me.role),
    h("button", { class: "btn btn--small", onclick: logOut }, "Log out"));
}

async function logOut() {
  try { await api("/auth/logout", { method: "POST" }); } catch { /* already logged out */ }
  me = null;
  location.hash = "#/login";
}

// --- pages -----------------------------------------------------------------------------

function loginPage() {
  frame();
  const email = h("input", { id: "email", type: "email", autocomplete: "username", required: true });
  const password = h("input", { id: "password", type: "password", autocomplete: "current-password", required: true });
  const error = h("p", { class: "bad small", role: "alert" });
  const form = h("form", { class: "card", onsubmit: async (event) => {
    event.preventDefault();
    error.textContent = "";
    try {
      const reply = await api("/auth/login", { method: "POST",
        body: { email: email.value, password: password.value, cookie: true } });
      me = reply.user;
      location.hash = "#/";
    } catch (err) {
      error.textContent = err.message;
    }
  } },
    h("div", { class: "field" }, h("label", { for: "email" }, "Email"), email),
    h("div", { class: "field" }, h("label", { for: "password" }, "Password"), password),
    error,
    h("button", { class: "btn btn--amber", type: "submit" }, "Log in"),
    h("p", { class: "muted small" }, "No account? Ask your Lumida admin."));
  show(h("div", { class: "login" },
    h("div", { class: "brand" }, h("img", { src: "mark.svg", alt: "", width: 34, height: 34 }), "lumida"),
    form));
  email.focus();
}

async function sourcesPage() {
  frame("sources");
  const sources = await api("/sources");
  const cards = sources.map((s) => {
    const total = Object.values(s.open_findings).reduce((a, b) => a + b, 0);
    const run = s.last_run;
    return h("a", { class: "card source-card", href: `#/s/${enc(s.name)}` },
      h("div", { class: "row" },
        h("div", {}, h("h2", {}, s.name), h("div", { class: "muted small" }, s.kind)),
        scoreView(s.health_score)),
      h("p", { class: "small" }, total ? `${total} open problem${total === 1 ? "" : "s"}` : "No open problems"),
      h("div", { class: "row" }, Object.entries(s.open_findings).map(([sev, n]) => badge(`${n} ${sev}`, `sev-${sev}`))),
      h("p", { class: "muted small" },
        run ? `Last run ${when(run.started_at)} · ${run.status}` : "Not run yet",
        h("br"),
        s.run_at ? `Runs daily at ${s.run_at} (${s.timezone})` : "Not scheduled"));
  });
  show(heading("Databases", null, can("admin") ? h("a", { class: "btn btn--amber", href: "#/add" }, "Add a database") : null),
    cards.length ? h("div", { class: "grid" }, cards)
      : h("div", { class: "card empty" }, "No databases yet. ",
        can("admin") ? h("a", { href: "#/add" }, "Add the first one") : "Ask an admin to add one", "."));
}

const TABS = [["", "Problems"], ["rules", "Rules"], ["relationships", "Relationships"],
  ["questions", "Questions"], ["runs", "Runs"]];

async function sourcePage(name, tab, query) {
  frame("sources");
  const source = await api(`/sources/${enc(name)}`);
  const shown = can("admin") ? [...TABS, ["settings", "Settings"]] : TABS;
  const tabs = h("nav", { class: "tabs", "aria-label": "Sections" },
    shown.map(([key, label]) => h("a", { href: `#/s/${enc(name)}${key ? `/${key}` : ""}`, class: key === tab ? "on" : "" }, label)));
  const pages = {
    "": () => findingsTab(source, query),
    rules: () => rulesTab(source),
    relationships: () => relationshipsTab(source, query),
    questions: () => questionsTab(source),
    runs: () => runsTab(source),
    settings: () => (can("admin") ? settingsTab(source) : h("div", { class: "card muted" }, "Settings need the admin role.")),
  };
  const body = await (pages[tab] || pages[""])();
  const status = h("span", { class: "small" });
  const controls = can("reviewer") ? jobButton(source.name, "run", "Run now", { line: status }) : [status];
  const [latest] = await api(`/sources/${enc(source.name)}/jobs`, { params: { limit: 1 } });
  if (latest && ACTIVE.has(latest.status)) watchJob(latest, status, () => route());
  show(heading(source.name, h("a", { href: "#/" }, "Databases"), ...controls, scoreView(source.health_score)),
    tabs, body);
}

async function healthCard(source) {
  let health;
  try {
    health = await api(`/sources/${enc(source.name)}/health`);
  } catch (err) {
    if (err.status === 404) return h("div", { class: "card muted" }, "No health score yet: it comes with the first nightly run.");
    throw err;
  }
  const change = health.previous_score === null ? null : health.source.score - health.previous_score;
  const worst = health.tables.filter((t) => t.score < 100).slice(0, 6);
  return h("div", { class: "card stack" },
    h("h2", {}, "Health"),
    h("p", { class: "muted small" }, `Computed ${when(health.computed_at)}`,
      change === null ? "" : ` · ${change >= 0 ? "+" : ""}${change.toFixed(1)} since the run before`),
    Object.entries(health.schemas).map(([schema, s]) => h("div", {},
      h("div", { class: "row small" }, h("span", {}, schema), h("span", { class: scoreClass(s.score) }, s.score.toFixed(0))),
      h("div", { class: "bar" }, h("span", { style: `width:${s.score}%;background:var(--${s.score >= 90 ? "teal" : s.score >= 70 ? "amber" : "coral"})` })))),
    worst.length ? h("div", {}, h("h3", {}, "Worst tables"),
      worst.map((t) => h("div", { class: "row small" }, h("span", { class: scoreClass(t.score) }, t.score.toFixed(0)), h("code", {}, t.table))))
      : h("p", { class: "small good" }, "Every table is at 100."));
}

const SEVERITIES = ["critical", "high", "medium", "low", "info"];
const CATEGORIES = ["structural", "relational", "column_value", "business_rule", "time_series",
  "row_outlier", "cross_system", "semantic", "db_health"];
const PAGE = 50;

async function findingsTab(source, query) {
  const status = query.get("status") || "active";
  const severity = query.get("severity") || "";
  const category = query.get("category") || "";
  const offset = Number(query.get("offset") || 0);
  const page = await api(`/sources/${enc(source.name)}/findings`,
    { params: { status, severity, category, limit: PAGE, offset } });
  const go = (change) => {
    const q = new URLSearchParams({ status, severity, category, offset: 0, ...change });
    location.hash = `#/s/${enc(source.name)}?${q}`;
  };
  const filters = h("div", { class: "row" },
    select("status", [["active", "Needs attention"], ["open", "Open"], ["confirmed", "Confirmed"],
      ["rejected", "Rejected"], ["resolved", "Resolved"], ["all", "All"]], status, (e) => go({ status: e.target.value })),
    select("severity", [["", "Any severity"], ...SEVERITIES.map((s) => [s, s])], severity, (e) => go({ severity: e.target.value })),
    select("category", [["", "Any kind"], ...CATEGORIES.map((c) => [c, c.replace("_", " ")])], category, (e) => go({ category: e.target.value })),
    h("span", { class: "muted small" }, `${page.total} problem${page.total === 1 ? "" : "s"}`));
  const rows = page.items.map((f) => [
    sevBadge(f.severity),
    h("div", {}, h("a", { href: `#/f/${f.id}` }, f.title),
      h("div", { class: "muted small" }, f.table ? h("code", {}, f.column ? `${f.table}.${f.column}` : f.table) : null, " ", f.check_name)),
    stBadge(f.status),
    h("span", { class: "small muted" }, when(f.last_detected_at)),
  ]);
  const pager = h("div", { class: "row" },
    h("button", { class: "btn btn--small", disabled: offset === 0, onclick: () => go({ offset: Math.max(0, offset - PAGE) }) }, "Previous"),
    h("span", { class: "muted small" }, page.total ? `${offset + 1}–${Math.min(offset + PAGE, page.total)} of ${page.total}` : ""),
    h("button", { class: "btn btn--small", disabled: offset + PAGE >= page.total, onclick: () => go({ offset: offset + PAGE }) }, "Next"));
  return h("div", { class: "split" },
    h("div", { class: "card stack" }, filters,
      table(["Severity", "Problem", "Status", "Last seen"], rows, "No problems match."), pager),
    await healthCard(source));
}

const REVIEWED = { confirm: "Confirmed", reject: "Rejected", resolve: "Marked resolved", reopen: "Reopened" };
const FINDING_ACTIONS = {
  open: [["confirm", "Confirm: it's real"], ["reject", "Reject: not a problem"], ["resolve", "Mark resolved"]],
  confirmed: [["resolve", "Mark resolved"], ["reject", "Reject: not a problem"]],
  rejected: [["reopen", "Reopen"]],
  resolved: [["reopen", "Reopen"]],
};

async function findingPage(id) {
  frame("sources");
  const f = await api(`/findings/${enc(id)}`);
  const note = h("textarea", { id: "note", placeholder: "Why? (kept in the history)", maxlength: 2000 });
  const review = can("reviewer")
    ? h("div", { class: "card stack" }, h("h2", {}, "Your review"), h("label", { for: "note" }, "Note"), note,
      h("div", { class: "row" }, (FINDING_ACTIONS[f.status] || []).map(([action, label], i) => {
        const button = h("button", { class: `btn${i === 0 ? " btn--amber" : ""}`, onclick: () => act(button,
          () => api(`/findings/${f.id}/review`, { method: "POST", body: { action, note: note.value || null } }),
          REVIEWED[action]) }, label);
        return button;
      })))
    : null;
  const e = f.explanation;
  const explanation = e ? h("div", { class: "card explain stack" },
    h("h3", {}, "AI explanation"),
    h("p", {}, e.summary),
    e.impact ? h("p", {}, h("strong", {}, "Impact: "), e.impact) : null,
    (e.likely_causes || []).length ? h("div", {}, h("strong", {}, "Possible causes"), h("ul", {}, e.likely_causes.map((c) => h("li", {}, c)))) : null,
    (e.next_steps || []).length ? h("div", {}, h("strong", {}, "What to check"), h("ul", {}, e.next_steps.map((c) => h("li", {}, c)))) : null,
    h("p", { class: "muted small" }, `${e.provider || "AI"}${e.model ? ` / ${e.model}` : ""}`,
      e.confidence !== undefined && e.confidence !== null ? ` · confidence ${Number(e.confidence).toFixed(2)}` : "",
      e.explained_title && e.explained_title !== f.title ? " · written for an earlier version of this problem" : "")) : null;
  const explainButton = about.ai_enabled && can("reviewer") && ["open", "confirmed"].includes(f.status)
    ? h("div", { class: "card row" }, ...jobButton(f.source, "explain", e ? "Explain again with AI" : "Explain with AI", { extra: { finding_id: f.id } }))
    : null;
  const history = f.history.map((ev) => [h("span", { class: "small" }, when(ev.at)), ev.by,
    h("span", {}, ev.from_status ? `${ev.from_status} → ` : "new → ", stBadge(ev.to_status)), ev.note || ""]);
  show(heading(f.title, h("a", { href: `#/s/${enc(f.source)}` }, f.source), sevBadge(f.severity), stBadge(f.status)),
    h("div", { class: "split" },
      h("div", { class: "stack" },
        h("div", { class: "card stack" },
          h("p", {}, f.description),
          h("p", { class: "muted small" }, `${f.category.replace("_", " ")} · ${f.check_name}`,
            f.table ? h("span", {}, " · ", h("code", {}, f.column ? `${f.table}.${f.column}` : f.table)) : null,
            ` · first seen ${when(f.first_detected_at)} · last seen ${when(f.last_detected_at)}`)),
        explanation, explainButton,
        h("div", { class: "card" }, h("h2", {}, "Evidence"), h("pre", {}, JSON.stringify(f.evidence, null, 2))),
        h("div", { class: "card" }, h("h2", {}, "History"), table(["When", "Who", "Change", "Note"], history))),
      review || h("div", { class: "card muted" }, "Reviewing needs the reviewer role.")));
}

const RULE_ACTIONS = {
  proposed: [["approve", "Approve"], ["reject", "Reject"]],
  active: [["disable", "Disable"]],
  disabled: [["enable", "Enable"]],
  rejected: [["reconsider", "Reconsider"]],
};
const RULE_ORDER = { proposed: 0, active: 1, disabled: 2, rejected: 3 };

async function rulesTab(source) {
  const rules = (await api(`/sources/${enc(source.name)}/rules`))
    .sort((a, b) => RULE_ORDER[a.status] - RULE_ORDER[b.status]);
  const proposed = rules.filter((r) => r.status === "proposed").length;
  const rows = rules.map((r) => [
    stBadge(r.status),
    h("details", {}, h("summary", {}, r.name),
      r.rationale ? h("p", { class: "muted small" }, r.rationale) : null,
      h("pre", {}, JSON.stringify(r.spec, null, 2))),
    h("span", { class: "small" }, r.origin, r.confidence === null ? "" : ` · ${r.confidence.toFixed(2)}`),
    can("reviewer") ? h("div", { class: "row" }, (RULE_ACTIONS[r.status] || []).map(([action, label]) => {
      const button = h("button", { class: `btn btn--small${action === "approve" ? " btn--amber" : ""}`,
        onclick: () => act(button, () => api(`/rules/${r.id}/review`, { method: "POST", body: { action } }), `${label}: done`) }, label);
      return button;
    })) : "",
  ]);
  const propose = about.ai_enabled && can("reviewer")
    ? h("div", { class: "row" }, ...jobButton(source.name, "propose_rules", "Propose rules with AI"),
      h("span", { class: "muted small" }, "The AI reads the structure and measurements (no row values) and suggests rules for you to review."))
    : null;
  const asCode = h("div", { class: "row" },
    h("a", { class: "btn btn--small", href: `${API}/sources/${enc(source.name)}/rules/export`, download: `lumida-rules-${source.name}.yaml` },
      "Download as YAML"),
    h("span", { class: "muted small" }, "Keep the approved rules in git; ", h("code", {}, "lumida check"), " runs them in your CI."));
  return h("div", { class: "card stack" }, propose,
    h("p", { class: "muted" }, proposed ? `${proposed} rule${proposed === 1 ? "" : "s"} waiting for review. Approved rules are checked every night as plain SQL.`
      : "Rules are checked every night as plain SQL. AI proposals appear here for review."),
    asCode,
    table(["Status", "Rule", "From", ""], rows, "No rules yet. Propose some with AI, or answer the questions."));
}

async function relationshipsTab(source, query) {
  const all = query.get("all") === "1";
  const rels = await api(`/sources/${enc(source.name)}/relationships`, { params: { include_rejected: all } });
  const rows = rels.map((r) => [
    h("code", {}, `${r.from_table}(${r.from_columns.join(", ")})`),
    h("code", {}, `${r.to_table}(${r.to_columns.join(", ")})`),
    h("span", { class: "small" }, r.kind === "declared" ? "foreign key" : `inferred · ${r.confidence.toFixed(2)}`,
      r.ai_review ? h("div", { class: "muted" }, `AI: ${r.ai_review.verdict}`) : null),
    stBadge(r.status),
    can("reviewer") && r.status === "proposed" ? h("div", { class: "row" }, [["confirm", "Confirm"], ["reject", "Reject"]].map(([action, label]) => {
      const button = h("button", { class: "btn btn--small", onclick: () => act(button,
        () => api(`/relationships/${r.id}/review`, { method: "POST", body: { action } }), `${label}ed`) }, label);
      return button;
    })) : "",
  ]);
  return h("div", { class: "card stack" },
    h("div", { class: "row" }, h("p", { class: "muted" }, "Links between tables: declared foreign keys, and links Lumida found from names, values and queries."),
      h("a", { href: `#/s/${enc(source.name)}/relationships${all ? "" : "?all=1"}` }, all ? "Hide rejected" : "Show rejected")),
    table(["From", "To", "Found by", "Status", ""], rows, "No relationships found yet."));
}

async function questionsTab(source) {
  const questions = await api(`/sources/${enc(source.name)}/questions`, { params: { limit: 20 } });
  if (!questions.length) return h("div", { class: "card empty" }, "No open questions: everything useful has been answered.");
  return h("div", { class: "stack" },
    h("p", { class: "muted" }, "Your answers teach Lumida what's normal here: they become checks, deadlines and importance levels."),
    questions.map((q) => {
      const input = h("input", { value: q.suggested, "aria-label": "answer", size: 32 });
      const send = (value, button) => act(button,
        () => api(`/sources/${enc(source.name)}/answers`, { method: "POST", body: { key: q.key, value } }),
        (reply) => reply.outcome);
      const answer = h("button", { class: "btn btn--amber btn--small", onclick: () => send(input.value, answer) }, "Answer");
      const skip = h("button", { class: "btn btn--small", onclick: () => send("?", skip) }, "Don't know");
      return h("div", { class: "card" }, h("p", {}, q.prompt), h("p", { class: "muted small" }, q.hint),
        can("reviewer") ? h("div", { class: "row" }, input, answer, skip) : h("p", { class: "muted small" }, "Answering needs the reviewer role."));
    }));
}

async function runsTab(source) {
  const [runs, jobs] = await Promise.all([
    api(`/sources/${enc(source.name)}/runs`, { params: { limit: 30 } }),
    api(`/sources/${enc(source.name)}/jobs`, { params: { limit: 20 } }),
  ]);
  return h("div", { class: "stack" },
    h("div", { class: "card" }, h("h2", {}, "Runs"), table(["Started", "Finished", "Status", "Problem"],
      runs.map((r) => [when(r.started_at), when(r.finished_at), stBadge(r.status), r.error_message || ""]),
      "No runs yet. Lumida runs every night at the scheduled time, or press Run now.")),
    h("div", { class: "card" }, h("h2", {}, "Asked for in the app"), table(["Asked", "What", "By", "Status", "Outcome"],
      jobs.map((j) => [when(j.requested_at), JOB_WORDS[j.kind] || j.kind, j.requested_by, stBadge(j.status),
        h("span", { class: "small" }, j.error || (j.result.summary || []).slice(0, 1).join(""))]),
      "Nothing asked for yet.")));
}

// --- a database's settings (admins) -----------------------------------------------------

async function settingsTab(source) {
  const field = (id, label, input, hint) => h("div", { class: "field" }, h("label", { for: id }, label), input,
    hint ? h("div", { class: "muted small" }, hint) : null);
  const runAt = h("input", { id: "set-run-at", type: "time", value: source.run_at || "" });
  const zone = h("input", { id: "set-zone", value: source.timezone });
  const include = h("input", { id: "set-include", value: (source.include_schemas || []).join(", "), placeholder: "all" });
  const exclude = h("input", { id: "set-exclude", value: (source.exclude_schemas || []).join(", "), placeholder: "none" });
  const list = (input) => input.value.split(",").map((s) => s.trim()).filter(Boolean);
  const save = h("form", { class: "card", onsubmit: (event) => {
    event.preventDefault();
    act(event.submitter, () => api(`/sources/${enc(source.name)}`, { method: "PATCH", body: {
      run_at: runAt.value || null, timezone: zone.value || "UTC",
      include_schemas: list(include), exclude_schemas: list(exclude) } }), "Saved");
  } },
    h("h2", {}, "Schedule and scope"),
    h("div", { class: "row" }, field("set-run-at", "Run every night at", runAt, "Empty = no schedule"), field("set-zone", "Time zone", zone)),
    h("div", { class: "row" }, field("set-include", "Only these schemas", include), field("set-exclude", "Leave out these schemas", exclude)),
    h("p", { class: "muted small" }, "Connection: ", h("code", {}, source.connection_ref || "—"), " (in the server's .env)."),
    h("button", { class: "btn btn--amber", type: "submit" }, "Save"));

  const pause = h("button", { class: "btn", onclick: () => act(pause,
    () => api(`/sources/${enc(source.name)}`, { method: "PATCH", body: { paused: !source.disabled } }),
    source.disabled ? "Nightly runs resumed" : "Paused: no nightly runs until you resume") },
  source.disabled ? "Resume nightly runs" : "Pause nightly runs");

  const confirm = h("input", { id: "remove-confirm", placeholder: source.name, autocomplete: "off" });
  const remove = h("button", { class: "btn btn--danger", onclick: async () => {
    if (confirm.value !== source.name) { toast(`Type ${source.name} to confirm`, true); return; }
    remove.disabled = true;
    try {
      await api(`/sources/${enc(source.name)}`, { method: "DELETE", params: { confirm: confirm.value } });
      toast(`${source.name} removed`);
      location.hash = "#/";
    } catch (err) {
      if (err.status !== 401) toast(err.message, true);
      remove.disabled = false;
    }
  } }, "Remove");

  return h("div", { class: "split" },
    save,
    h("div", { class: "stack" },
      h("div", { class: "card stack" }, h("h2", {}, "Pause"),
        h("p", { class: "muted small" }, source.disabled
          ? "Paused: Lumida doesn't run this database at night. Run now still works."
          : "Stop the nightly runs for a while (e.g. during a migration). Nothing is deleted."),
        pause),
      h("div", { class: "card stack danger" }, h("h2", {}, "Remove"),
        h("p", { class: "muted small" }, "Forget everything Lumida learned about this database: its history, problems, rules and answers. The database itself is never touched. This can't be undone."),
        h("label", { for: "remove-confirm" }, `Type ${source.name} to confirm`), h("div", { class: "row" }, confirm, remove))));
}

// --- adding a database (admins) ---------------------------------------------------------

const KIND_NAMES = { postgres: "PostgreSQL", mysql: "MySQL / MariaDB", sqlserver: "SQL Server", oracle: "Oracle", snowflake: "Snowflake" };
const ZONES = ["UTC", "Europe/London", "Europe/Berlin", "America/New_York", "America/Chicago", "America/Los_Angeles",
  "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney"];

async function addPage() {
  frame("sources");
  const connections = await api("/connections");
  const field = (id, label, input, hint) => h("div", { class: "field" }, h("label", { for: id }, label), input,
    hint ? h("div", { class: "muted small" }, hint) : null);
  const name = h("input", { id: "src-name", required: true, pattern: "[A-Za-z0-9_.-]+", placeholder: "e.g. shop" });
  const kind = h("select", { id: "src-kind" }, Object.entries(KIND_NAMES).map(([k, label]) => h("option", { value: k }, label)));
  const connection = h("select", { id: "src-conn" });
  const include = h("input", { id: "src-include", placeholder: "all" });
  const exclude = h("input", { id: "src-exclude", placeholder: "none" });
  const runAt = h("input", { id: "src-run-at", type: "time", value: "03:00" });
  const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const zone = h("input", { id: "src-zone", value: localZone, list: "zones" });
  const zones = h("datalist", { id: "zones" }, [...new Set([localZone, ...ZONES])].map((z) => h("option", { value: z })));
  const script = h("pre");
  const testLine = h("div", { class: "small" });
  const error = h("p", { class: "bad small", role: "alert" });

  const fillConnections = () => {
    const matching = connections.filter((c) => c.kind === kind.value);
    connection.replaceChildren(...(matching.length ? matching.map((c) => h("option", { value: c.name }, c.name))
      : [h("option", { value: "" }, "none of this kind in .env")]));
  };
  const fillScript = async () => {
    script.textContent = (await api(`/setup-scripts/${kind.value}`)).script;
  };
  kind.addEventListener("change", () => { fillConnections(); fillScript(); testLine.replaceChildren(); });
  const list = (input) => input.value.split(",").map((s) => s.trim()).filter(Boolean);
  const scope = () => ({ include_schemas: list(include), exclude_schemas: list(exclude) });

  const test = h("button", { class: "btn", type: "button", onclick: async () => {
    if (!connection.value) { toast("Pick a connection first", true); return; }
    test.disabled = true;
    try {
      const job = await api("/connection-tests", { method: "POST", body: { kind: kind.value, connection_ref: connection.value, ...scope() } });
      await watchJob(job, testLine, async (j) => {
        if (j.status !== "succeeded") return;
        const schemas = Object.entries(j.result.schemas).map(([s, n]) => `${s} (${n})`).join(", ");
        testLine.replaceChildren(stBadge("succeeded"), ` Connected read-only. Can see ${j.result.tables} tables`,
          schemas ? ` in ${schemas}.` : ". Nothing to watch: check the schemas and the login's grants.");
      });
    } catch (err) {
      if (err.status !== 401) toast(err.message, true);
    } finally { test.disabled = false; }
  } }, "Test connection");
  const runNow = h("input", { type: "checkbox", id: "src-run-now", checked: true });

  const form = h("form", { class: "card", onsubmit: async (event) => {
    event.preventDefault();
    error.textContent = "";
    try {
      const added = await api("/sources", { method: "POST", body: {
        name: name.value, kind: kind.value, connection_ref: connection.value,
        run_at: runAt.value || null, timezone: zone.value || "UTC", ...scope() } });
      if (runNow.checked) await startJob(added.name, "run");
      toast(`${added.name} added${runNow.checked ? "; the first run has started" : ""}`);
      location.hash = `#/s/${enc(added.name)}`;
    } catch (err) {
      if (err.status !== 401) error.textContent = err.message;
    }
  } },
    h("h2", {}, "The database"),
    field("src-name", "Name in Lumida", name),
    field("src-kind", "Kind", kind),
    field("src-conn", "Connection", connection,
      "The connection URL, with its password, stays in the server's .env file; pick it by name here."),
    h("div", { class: "row" }, field("src-include", "Only these schemas (comma-separated)", include),
      field("src-exclude", "Leave out these schemas", exclude)),
    h("div", { class: "row" }, field("src-run-at", "Run every night at", runAt, "Empty = no schedule"),
      field("src-zone", "Time zone", zone), zones),
    h("div", { class: "row" }, test), testLine,
    h("div", { class: "field row" }, runNow, h("label", { for: "src-run-now" },
      "Run it right after adding (the first run learns what's normal; it doesn't alert)")),
    error,
    h("button", { class: "btn btn--amber", type: "submit" }, "Add database"));

  const copy = h("button", { class: "btn btn--small", type: "button", onclick: async () => {
    try { await navigator.clipboard.writeText(script.textContent); toast("Copied"); } catch { toast("Select the text and copy it", true); }
  } }, "Copy");
  const help = h("div", { class: "stack" },
    h("div", { class: "card stack" }, h("h2", {}, "1. A read-only login"),
      h("p", { class: "muted small" }, "Run this on the database (fill in the <placeholders>). It grants reading only."),
      script, copy),
    h("div", { class: "card stack" }, h("h2", {}, "2. Its URL on the Lumida server"),
      h("p", { class: "muted small" }, "Add one line to the server's .env under a name you choose (example at the end of the script), then restart Lumida: ",
        h("code", {}, "docker compose up -d")),
      h("p", { class: "muted small" }, connections.length
        ? `Found in .env: ${connections.map((c) => `${c.name} (${c.kind})`).join(", ")}.`
        : "No database URLs found in .env yet.")));
  fillConnections();
  await fillScript();
  show(heading("Add a database", h("a", { href: "#/" }, "Databases")), h("div", { class: "split" }, form, help));
  name.focus();
}

async function peoplePage() {
  frame("people");
  const users = await api("/users");
  const field = (id, label, input) => h("div", { class: "field" }, h("label", { for: id }, label), input);
  const email = h("input", { id: "new-email", type: "email", required: true });
  const name = h("input", { id: "new-name" });
  const role = select("role", [["viewer", "viewer"], ["reviewer", "reviewer"], ["admin", "admin"]], "viewer");
  const password = h("input", { id: "new-password", type: "password", minlength: 12, required: true, autocomplete: "new-password" });
  const add = h("form", { class: "card", onsubmit: (event) => {
    event.preventDefault();
    act(event.submitter, () => api("/users", { method: "POST",
      body: { email: email.value, name: name.value, role: role.value, password: password.value } }), "Account created");
  } },
    h("h2", {}, "Add a person"), field("new-email", "Email", email), field("new-name", "Name", name),
    h("div", { class: "field" }, h("label", {}, "Role"), role),
    field("new-password", "First password (12+ characters; tell them to change it)", password),
    h("button", { class: "btn btn--amber", type: "submit" }, "Create account"));
  const rows = users.map((u) => {
    const self = u.email === me.email;
    const roleSelect = select(`role of ${u.email}`, [["viewer", "viewer"], ["reviewer", "reviewer"], ["admin", "admin"]], u.role,
      (e) => act(e.target, () => api(`/users/${u.id}`, { method: "PATCH", body: { role: e.target.value } }), "Role changed"));
    roleSelect.disabled = self;
    const toggle = h("button", { class: `btn btn--small${u.disabled ? "" : " btn--danger"}`, disabled: self,
      onclick: () => act(toggle, () => api(`/users/${u.id}`, { method: "PATCH", body: { disabled: !u.disabled } }),
        u.disabled ? "Enabled" : "Disabled; their sessions ended") }, u.disabled ? "Enable" : "Disable");
    const newPassword = h("input", { type: "password", minlength: 12, autocomplete: "new-password",
      placeholder: "new password", "aria-label": `new password for ${u.email}`, hidden: true });
    const reset = h("button", { class: "btn btn--small", onclick: () => {
      if (newPassword.hidden) { newPassword.hidden = false; reset.textContent = "Set password"; newPassword.focus(); return; }
      act(reset, () => api(`/users/${u.id}/password`, { method: "POST", body: { password: newPassword.value } }),
        "Password set; their sessions ended");
    } }, "Reset password");
    return [h("div", {}, u.email, h("div", { class: "muted small" }, u.name)), roleSelect,
      h("span", { class: "small" }, u.disabled ? badge("disabled", "st-disabled") : u.locked ? badge("locked", "st-failed") : "active"),
      h("span", { class: "small muted" }, when(u.last_login_at)), h("div", { class: "row" }, toggle, newPassword, reset)];
  });
  show(heading("People"), h("div", { class: "split" },
    h("div", { class: "card" }, table(["Person", "Role", "State", "Last login", ""], rows)), add));
}

async function auditPage(query) {
  frame("audit");
  const action = query.get("action") || "";
  const offset = Number(query.get("offset") || 0);
  const events = await api("/audit", { params: { action, limit: PAGE, offset } });
  const go = (change) => { location.hash = `#/audit?${new URLSearchParams({ action, offset: 0, ...change })}`; };
  const filter = select("action", [["", "Everything"], ["auth", "Logins"], ["finding", "Problem reviews"],
    ["rule", "Rules"], ["relationship", "Relationships"], ["question", "Answers"], ["user", "People"], ["key", "API keys"]],
  action, (e) => go({ action: e.target.value }));
  const rows = events.map((e) => [h("span", { class: "small" }, when(e.at)), e.actor, h("code", {}, e.action),
    h("span", { class: "small muted" }, Object.entries(e.details).map(([k, v]) => `${k}: ${v}`).join(" · "))]);
  show(heading("Audit log", null, filter), h("div", { class: "card stack" },
    table(["When", "Who", "What", "Details"], rows, "Nothing recorded yet."),
    h("div", { class: "row" },
      h("button", { class: "btn btn--small", disabled: offset === 0, onclick: () => go({ offset: Math.max(0, offset - PAGE) }) }, "Newer"),
      h("button", { class: "btn btn--small", disabled: events.length < PAGE, onclick: () => go({ offset: offset + PAGE }) }, "Older"))));
}

async function mePage() {
  frame("me");
  const keys = await api("/auth/keys");
  const current = h("input", { id: "pw-current", type: "password", autocomplete: "current-password", required: true });
  const fresh = h("input", { id: "pw-new", type: "password", autocomplete: "new-password", minlength: 12, required: true });
  const passwordForm = h("form", { class: "card", onsubmit: async (event) => {
    event.preventDefault();
    try {
      await api("/auth/password", { method: "POST", body: { current_password: current.value, new_password: fresh.value } });
      toast("Password changed. Please log in again.");
      me = null;
      location.hash = "#/login";
    } catch (err) {
      if (err.status !== 401) toast(err.message, true);
    }
  } },
    h("h2", {}, "Change password"),
    h("div", { class: "field" }, h("label", { for: "pw-current" }, "Current password"), current),
    h("div", { class: "field" }, h("label", { for: "pw-new" }, "New password (12+ characters)"), fresh),
    h("button", { class: "btn btn--amber", type: "submit" }, "Change password"));
  const label = h("input", { "aria-label": "key label", placeholder: "e.g. jira sync", maxlength: 200 });
  const made = h("div");
  if (newKey) {
    made.append(h("div", { class: "stack" },
      h("p", { class: "small" }, "Your new key. Copy it now: it won't be shown again."),
      h("div", { class: "keybox" }, newKey),
      h("p", { class: "muted small" }, "Send it as Authorization: Bearer <key>.")));
    newKey = null; // shown once
  }
  const create = h("button", { class: "btn btn--amber btn--small", onclick: async () => {
    if (!label.value.trim()) { toast("Give the key a name first", true); return; }
    try {
      newKey = (await api("/auth/keys", { method: "POST", body: { label: label.value } })).key;
      await mePage();
    } catch (err) { toast(err.message, true); }
  } }, "Create key");
  const rows = keys.map((k) => {
    const revoke = h("button", { class: "btn btn--small btn--danger", onclick: () => act(revoke,
      () => api(`/auth/keys/${k.id}`, { method: "DELETE" }), "Key revoked") }, "Revoke");
    return [k.label, when(k.created_at), when(k.last_used_at), revoke];
  });
  show(heading("My account", null, h("span", { class: "muted" }, `${me.email} · ${me.role}`)),
    h("div", { class: "split" },
      h("div", { class: "card stack" }, h("h2", {}, "API keys"),
        h("p", { class: "muted small" }, "For scripts: a key acts as you, with your role, until you revoke it."),
        h("div", { class: "row" }, label, create), made,
        table(["Name", "Created", "Last used", ""], rows, "No keys.")),
      passwordForm));
}

// --- first-run setup (0.4.1) -------------------------------------------------------------
// Shown before anything else while Lumida's memory or its first admin is missing. Every
// step sends the one-time setup code printed where Lumida was started.

let setupDone = false;
let setupCode = "";

async function setupStatus() {
  try {
    const response = await fetch("/api/setup/status", { credentials: "same-origin" });
    return response.ok ? await response.json() : { ready: true };
  } catch {
    return { ready: false, starting: true }; // Lumida is restarting after setup
  }
}

async function setupCall(path, body) {
  const response = await fetch(`/api/setup${path}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Lumida-Setup-Code": setupCode },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail
      : Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : response.statusText;
    throw new ApiError(response.status, detail);
  }
  return data;
}

function setupShell(step, ...content) {
  frame();
  const steps = [["memory", "Lumida's memory"], ["admin", "Your admin account"], ["done", "Start"]];
  return show(h("div", { class: "setup" },
    h("div", { class: "brand" }, h("img", { src: "mark.svg", alt: "", width: 34, height: 34 }), "lumida"),
    h("h1", {}, "Set up Lumida"),
    h("p", { class: "muted" }, "Everything stays on this machine. Nothing is sent to Lumida's makers."),
    h("ol", { class: "setup__steps" }, steps.map(([key, label]) =>
      h("li", { class: key === step ? "on" : "" }, label))),
    ...content));
}

function codeField() {
  const input = h("input", { id: "setup-code", value: setupCode, placeholder: "1234-5678", autocomplete: "off",
    oninput: (e) => { setupCode = e.target.value.trim(); } });
  return h("div", { class: "field" }, h("label", { for: "setup-code" }, "Setup code"), input,
    h("div", { class: "muted small" }, "Printed where Lumida was started (the terminal, or docker compose logs lumida)."));
}

async function setupPage(status) {
  if (status.starting || (status.setup_mode && !status.needs_memory && !status.needs_admin)) {
    setupShell("done", h("div", { class: "card stack" }, h("h2", {}, "Starting Lumida…"),
      h("p", { class: "muted" }, "This takes a few seconds. The page continues by itself.")));
    setTimeout(route, 1500);
    return;
  }
  if (status.needs_memory) return memoryStep(status);
  return adminStep();
}

function memoryStep(status) {
  const error = h("p", { class: "bad small", role: "alert" });
  const result = h("p", { class: "small" });
  const field = (id, label, input) => h("div", { class: "field" }, h("label", { for: id }, label), input);
  const host = h("input", { id: "pg-host", value: "localhost" });
  const port = h("input", { id: "pg-port", value: "5432", inputmode: "numeric" });
  const database = h("input", { id: "pg-db", value: "lumida" });
  const user = h("input", { id: "pg-user", value: "postgres" });
  const password = h("input", { id: "pg-password", type: "password", autocomplete: "off" });
  const existing = () => ({ host: host.value.trim(), port: Number(port.value), database: database.value.trim(),
    user: user.value.trim(), password: password.value });
  const fields = h("div", { class: "setup__fields", hidden: true },
    h("div", { class: "row" }, field("pg-host", "Host", host), field("pg-port", "Port", port)),
    h("div", { class: "row" }, field("pg-db", "Database (created if missing)", database), field("pg-user", "User", user)),
    field("pg-password", "Password", password));

  const option = (value, title, hint, disabled = false) => h("label", { class: `choice${disabled ? " choice--off" : ""}` },
    h("input", { type: "radio", name: "memory", value, disabled, onchange: () => { fields.hidden = value !== "existing"; result.textContent = ""; } }),
    h("span", {}, h("strong", {}, title), h("span", { class: "muted small" }, hint)));
  const dockerHint = status.docker ? "Recommended. Lumida starts a private Postgres in Docker (about 20 seconds)."
    : "Needs Docker, which isn't running on this machine. Start Docker Desktop and reload, or use a Postgres you have.";
  const choices = h("div", { class: "stack" },
    option("docker", "Start one for me", dockerHint, !status.docker),
    option("existing", "Use a Postgres I already have", "Any Postgres you can reach, e.g. a spare database on an existing server."),
    option("none", "Without a separate database", "Coming in a later version.", true));
  const chosen = () => (choices.querySelector("input[name=memory]:checked") || {}).value;

  const test = h("button", { class: "btn", type: "button", onclick: async () => {
    error.textContent = ""; result.textContent = "Testing…"; test.disabled = true;
    try {
      const found = await setupCall("/memory/test", existing());
      result.className = `small ${found.ok ? "good" : "bad"}`;
      result.textContent = found.message;
    } catch (err) { result.textContent = ""; error.textContent = err.message; }
    finally { test.disabled = false; }
  } }, "Test connection");
  fields.append(h("div", { class: "row" }, test), result);

  const go = h("button", { class: "btn btn--amber", type: "submit" }, "Continue");
  const form = h("form", { class: "card stack", onsubmit: async (event) => {
    event.preventDefault();
    error.textContent = "";
    const mode = chosen();
    if (!mode) { error.textContent = "Choose where Lumida keeps what it learns."; return; }
    go.disabled = true;
    go.textContent = mode === "docker" ? "Starting Postgres… (about 20 s)" : "Preparing…";
    try {
      const next = await setupCall("/memory", mode === "docker" ? { mode } : { mode, existing: existing() });
      setupPage(next);
    } catch (err) {
      error.textContent = err.message;
      go.disabled = false; go.textContent = "Continue";
    }
  } },
    codeField(),
    h("h2", {}, "Where should Lumida keep what it learns?"),
    h("p", { class: "muted small" }, "Lumida remembers each table's measurements over time, the problems it found and your decisions. It needs a small Postgres of its own; it never writes to the databases it watches."),
    choices, fields, error, go);
  setupShell("memory", form);
}

function adminStep() {
  const error = h("p", { class: "bad small", role: "alert" });
  const field = (id, label, input) => h("div", { class: "field" }, h("label", { for: id }, label), input);
  const name = h("input", { id: "admin-name", autocomplete: "name" });
  const email = h("input", { id: "admin-email", type: "email", required: true, autocomplete: "username" });
  const password = h("input", { id: "admin-password", type: "password", minlength: 12, required: true, autocomplete: "new-password" });
  const again = h("input", { id: "admin-again", type: "password", minlength: 12, required: true, autocomplete: "new-password" });
  const go = h("button", { class: "btn btn--amber", type: "submit" }, "Create admin");
  const form = h("form", { class: "card stack", onsubmit: async (event) => {
    event.preventDefault();
    error.textContent = "";
    if (password.value !== again.value) { error.textContent = "The two passwords differ."; return; }
    go.disabled = true;
    try {
      const next = await setupCall("/admin", { name: name.value, email: email.value, password: password.value });
      toast("Admin created. Log in with it.");
      setupCode = "";
      if (next.ready) { setupDone = true; location.hash = "#/login"; return route(); }
      setupPage({ ...next, starting: true });
    } catch (err) {
      error.textContent = err.message;
      go.disabled = false;
    }
  } },
    codeField(),
    h("h2", {}, "Your admin account"),
    h("p", { class: "muted small" }, "The first person who can log in. Admins add colleagues later under People."),
    field("admin-name", "Name", name), field("admin-email", "Email", email),
    field("admin-password", "Password (12+ characters)", password), field("admin-again", "Password again", again),
    error, go);
  setupShell("admin", form);
}

// --- routing ---------------------------------------------------------------------------

async function route() {
  const [path, search = ""] = location.hash.replace(/^#/, "").split("?");
  const query = new URLSearchParams(search);
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  if (!setupDone) {
    const status = await setupStatus();
    if (!status.ready) return setupPage(status);
    setupDone = true;
  }
  if (parts[0] === "login") return loginPage();
  if (!me) {
    try {
      me = await api("/auth/me");
    } catch {
      return; // api() has sent us to the login page
    }
  }
  if (!about) about = await api("/about").catch(() => ({ ai_enabled: false }));
  try {
    if (parts.length === 0) return await sourcesPage();
    if (parts[0] === "add" && can("admin")) return await addPage();
    if (parts[0] === "s" && parts[1]) return await sourcePage(parts[1], parts[2] || "", query);
    if (parts[0] === "f" && parts[1]) return await findingPage(parts[1]);
    if (parts[0] === "people" && can("admin")) return await peoplePage();
    if (parts[0] === "audit" && can("admin")) return await auditPage(query);
    if (parts[0] === "me") return await mePage();
    show(h("div", { class: "card empty" }, "Page not found. ", h("a", { href: "#/" }, "Back to the databases")));
  } catch (err) {
    if (err.status === 401) return;
    show(heading("Something went wrong"), h("div", { class: "card" }, h("p", {}, err.message),
      h("a", { href: "#/" }, "Back to the databases")));
  }
}

window.addEventListener("hashchange", route);
route();
