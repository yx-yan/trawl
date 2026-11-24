// trawl desktop: jobs, one job in full, and the funnel. Plain JS over the local API.
"use strict";

const STAGES = [
  ["applied", "Applied"], ["interview", "Interview"], ["offer", "Offer"],
  ["rejected", "Rejected"], ["withdrawn", "Withdrawn"], ["skipped", "Skip"],
];
const FILTERS = {
  todo: { label: "To do", test: j => j.open && (j.status === "new" || j.status === "prepared") },
  progress: { label: "In progress", test: j => ["applied", "interview", "offer"].includes(j.status) },
  done: { label: "Done", test: j => ["rejected", "withdrawn", "skipped"].includes(j.status)
                                   || (!j.open && (j.status === "new" || j.status === "prepared")) },
  all: { label: "All", test: () => true },
};
const BOARDS = { greenhouse: "Greenhouse", ashby: "Ashby", lever: "Lever", workday: "Workday",
                 smartrecruiters: "SmartRecruiters", mcf: "MyCareersFuture" };

const state = { jobs: [], filter: "todo", source: "", q: "", selected: null, following: {}, last: {} };
const $main = document.getElementById("main");

// --- helpers ------------------------------------------------------------------------------------

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const thisYear = new Date().getFullYear();
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const opts = { month: "short", day: "numeric", ...(d.getFullYear() !== thisYear && { year: "numeric" }) };
  return d.toLocaleDateString("en-GB", opts);
}
function fmtTime(iso) {
  const d = new Date(iso);
  return `${fmtDate(iso)} ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
}
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}
const scoreClass = s => s == null ? "" : s >= 70 ? "hi" : s >= 60 ? "mid" : "";
const scoreBox = s => `<div class="score ${scoreClass(s)}">${s ?? "–"}</div>`;
// MyCareersFuture gives registered names: "DCS CARD CENTRE PTE. LTD." reads as "DCS Card Centre"
const LEGAL = /[\s,]*(\((singapore )?branch\)|\(private\)|singapore pte\.? ltd\.?|pte\.? ltd\.?|private limited|limited|ltd\.?|inc\.?|llc)\s*$/i;
function company(name) {
  let n = String(name || "").trim();
  for (let i = 0; i < 3 && LEGAL.test(n); i++) n = n.replace(LEGAL, "").trim();
  if (n && n === n.toUpperCase()) {
    n = n.split(/\s+/).map(w => w.length <= 3 && /^[A-Z&]+$/.test(w) ? w : w[0] + w.slice(1).toLowerCase()).join(" ");
  }
  return n || name;
}
const pill = status => `<span class="pill ${esc(status)}">${esc(status)}</span>`;

// --- routing ------------------------------------------------------------------------------------

function route() {
  const [, view = "jobs", id] = location.hash.split("/");
  document.querySelectorAll(".nav a").forEach(a => a.classList.toggle("on", a.dataset.view === view));
  if (view === "stats") return showStats();
  state.selected = id ? Number(id) : state.selected;
  if (document.querySelector(".jobs") && state.jobs.length && id) return showJob(state.selected);
  showJobs();
}
window.addEventListener("hashchange", route);

// --- jobs ---------------------------------------------------------------------------------------

async function showJobs() {
  if (!document.querySelector(".jobs")) {
    $main.innerHTML = `
      <div class="jobs">
        <div class="list">
          <div class="toolbar">
            <div class="seg" id="filters"></div>
            <select id="source"><option value="">All boards</option>
              ${Object.entries(BOARDS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
            <input id="q" type="search" placeholder="Search company or title">
          </div>
          <div class="rows" id="rows"></div>
        </div>
        <div class="detail" id="detail"><div class="placeholder">Select a job</div></div>
      </div>`;
    document.getElementById("source").onchange = e => { state.source = e.target.value; renderRows(); };
    document.getElementById("q").oninput = e => { state.q = e.target.value.toLowerCase(); renderRows(); };
  }
  try {
    state.jobs = await api("/api/jobs");
    state.projects ||= await api("/api/projects").catch(() => null);
  } catch (e) {
    $main.innerHTML = `<div class="error">Can't load jobs: ${esc(e.message)}</div>`;
    return;
  }
  renderRows();
  if (state.selected) showJob(state.selected);
}

function visible() {
  return state.jobs.filter(j => FILTERS[state.filter].test(j)
    && (!state.source || j.source === state.source)
    && (!state.q || `${j.company} ${company(j.company)} ${j.title}`.toLowerCase().includes(state.q)));
}

function renderRows() {
  const counts = Object.fromEntries(Object.entries(FILTERS).map(([k, f]) => [k, state.jobs.filter(f.test).length]));
  document.getElementById("filters").innerHTML = Object.entries(FILTERS).map(([k, f]) =>
    `<button data-f="${k}" class="${k === state.filter ? "on" : ""}">${f.label}<span class="n">${counts[k]}</span></button>`).join("");
  document.querySelectorAll("#filters button").forEach(b => b.onclick = () => { state.filter = b.dataset.f; renderRows(); });
  const rows = visible();
  document.getElementById("rows").innerHTML = rows.length ? rows.map(j => `
    <div class="row ${j.id === state.selected ? "on" : ""} ${j.open ? "" : "closed"}" data-id="${j.id}">
      ${scoreBox(j.score)}
      <div class="what">
        <div class="company">${esc(company(j.company))} ${j.track === "fde" ? '<span class="tag">FDE</span>' : ""}
          ${j.has_leads ? '<span class="tag" title="Found where to apply">↗ lead</span>' : ""}</div>
        <div class="title">${esc(j.title)}</div>
      </div>
      <div class="meta">${pill(j.status)}<span class="when">${esc(BOARDS[j.source] || j.source)} · ${fmtDate(j.first_seen)}</span></div>
    </div>`).join("") : `<div class="empty">Nothing here.</div>`;
  document.querySelectorAll(".row").forEach(r => r.onclick = () => { location.hash = `#/jobs/${r.dataset.id}`; });
}

async function showJob(id) {
  document.querySelectorAll(".row").forEach(r => r.classList.toggle("on", Number(r.dataset.id) === id));
  const $d = document.getElementById("detail");
  let j;
  try {
    j = await api(`/api/jobs/${id}`);
  } catch (e) {
    $d.innerHTML = `<div class="placeholder">${esc(e.message)}</div>`;
    return;
  }
  const m = j.match || {};
  $d.innerHTML = `
    <h1>${esc(j.title)}</h1>
    <div class="sub"><span title="${esc(j.company)}">${esc(company(j.company))}</span><span>${esc(j.location)}</span>
      <span>${esc(BOARDS[j.source] || j.source)}</span>${j.open ? "" : "<span>closed</span>"}
      <a href="${esc(j.url)}" target="_blank">Posting ↗</a></div>
    <div>${pill(j.status)} <span class="tag">#${j.id}</span> ${j.track ? `<span class="tag">${esc(j.track)}</span>` : ""}
      ${(j.flags || []).map(f => `<span class="tag">${esc(f)}</span>`).join(" ")}</div>

    ${actions(j)}

    <section><h2>Fit</h2>
      <div class="fit">
        <span class="k">Score</span><span>${m.score ?? "not scored"}</span>
        ${m.strengths?.length ? `<span class="k">Strengths</span><span>${m.strengths.map(esc).join(", ")}</span>` : ""}
        ${m.gaps?.length && !j.requirements?.length ? `<span class="k">Gaps</span><span>${m.gaps.map(esc).join(", ")}</span>` : ""}
        ${m.angle ? `<span class="k">Angle</span><span>${esc(m.angle)}</span>` : ""}
      </div>
    </section>

    ${requirements(j)}

    <section><h2>Progress</h2>
      ${j.stages.length ? `<ul class="plain stages">${j.stages.map(s => `
        <li><span class="at">${fmtDate(s.at)}</span>${pill(s.stage)}
          <span class="note">${s.by_trawl ? "submitted by trawl" : esc(s.note)}</span>
          ${s.by_trawl ? "" : `<button class="undo" title="Undo" data-stage="${s.id}">✕</button>`}</li>`).join("")}</ul>`
        : `<div class="k" style="color:var(--faint)">Nothing recorded yet.</div>`}
      <div class="actions">
        <input id="note" placeholder="Note (optional): where, who, when">
        ${STAGES.map(([k, v]) => `<button class="btn" data-add="${k}">${v}</button>`).join("")}
      </div>
    </section>

    ${j.application ? application(j) : ""}
    ${j.screenshots.length ? `<section><h2>Screenshots</h2><div class="shots">${j.screenshots.map(n =>
      `<img src="/api/jobs/${j.id}/screenshots/${esc(n)}" alt="${esc(n)}" title="${esc(n)}">`).join("")}</div></section>` : ""}
    ${j.audit.length ? `<section><h2>Activity</h2><ul class="plain audit">${j.audit.map(a => `
      <li><span>${fmtTime(a.at)}</span><span class="ev">${esc(a.event.replace(/_/g, " "))}</span>
        <span class="d" title="${esc(JSON.stringify(a.detail))}">${esc(summary(a))}</span></li>`).join("")}</ul></section>` : ""}`;

  $d.querySelectorAll("[data-add]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    try {
      await api(`/api/jobs/${j.id}/stages`, { method: "POST", body: JSON.stringify({ stage: b.dataset.add, note: document.getElementById("note").value }) });
      await refresh(j.id);
    } catch (e) { alert(e.message); b.disabled = false; }
  });
  $d.querySelectorAll("[data-stage]").forEach(b => b.onclick = async () => {
    try { await api(`/api/stages/${b.dataset.stage}`, { method: "DELETE" }); await refresh(j.id); } catch (e) { alert(e.message); }
  });
  $d.querySelectorAll(".shots img").forEach(img => img.onclick = () => lightbox(img.src));
  $d.querySelectorAll("[data-act]").forEach(b => b.onclick = () => act(j, b.dataset.act, b));
  const cmp = document.getElementById("compare");
  if (cmp) cmp.onclick = async () => {
    cmp.disabled = true;
    cmp.textContent = "Comparing…";
    try { await api(`/api/jobs/${j.id}/requirements`, { method: "POST" }); await refreshIfOpen(j.id); }
    catch (e) { alert(e.message); cmp.disabled = false; cmp.textContent = "Compare with my résumé"; }
  };
  const find = document.getElementById("find");
  if (find) find.onclick = async () => {
    find.disabled = true;
    find.textContent = "Looking…";
    try { await api(`/api/jobs/${j.id}/leads`, { method: "POST" }); await refresh(j.id); }
    catch (e) { alert(e.message); find.disabled = false; find.textContent = "Find where to apply"; }
  };
  watchPicker();
  if (j.task) follow(j.task, j.id);
  else if (state.last[j.id]) renderTask(state.last[j.id]);
  const toggle = document.getElementById("show-pdf");
  if (toggle) toggle.onclick = () => {
    const box = document.getElementById("pdf-box");
    box.innerHTML = box.innerHTML ? "" : `<iframe class="pdf" src="/api/jobs/${j.id}/resume.pdf"></iframe>`;
    toggle.textContent = box.innerHTML ? "Hide résumé" : "Show résumé";
  };
}

// --- requirements against the résumé ------------------------------------------------------------

const KINDS = { language: "Languages", ai: "AI & ML", engineering: "Engineering", cloud: "Cloud & DevOps",
                domain: "Domain", customers: "Customers", education: "Background" };
const STATUS = { have: ["✓", "Have"], bank: ["＋", "Have, not on résumé"], partial: ["◐", "Partial"], missing: ["✕", "Missing"] };

function requirements(j) {
  const rows = j.requirements;
  if (!rows?.length) return `<section><h2>Requirements vs résumé</h2>
    <div class="hint" style="margin:0 0 8px">${rows ? "No concrete requirements found in this posting." : "Not compared yet."}</div>
    <button class="btn" id="compare">Compare with my résumé</button></section>`;
  const n = s => rows.filter(r => r.status === s).length;
  const musts = rows.filter(r => r.must);
  const line = r => `<tr class="${r.status}">
      <td class="st" title="${STATUS[r.status][1]}">${STATUS[r.status][0]}<span class="sr">${STATUS[r.status][1]}</span></td>
      <td title="Posting: “${esc(r.quote)}”">${esc(r.skill)}</td>
      <td>${r.must ? "Must" : '<span class="nice">Nice</span>'}</td>
      <td class="ev">${r.evidence ? esc(r.evidence) : "–"}${r.in_bank ? ' <span class="tag">bank</span>' : ""}</td></tr>`;
  const groups = Object.entries(KINDS).map(([k, label]) => {
    const of = rows.filter(r => r.kind === k).sort((a, b) => b.must - a.must);
    return of.length ? `<tr class="kind"><th colspan="4" scope="colgroup">${label}</th></tr>${of.map(line).join("")}` : "";
  }).join("");
  return `<section><h2>Requirements vs résumé</h2>
    <div class="cover" aria-hidden="true">${Object.keys(STATUS).map(s => n(s) ? `<i class="${s}" style="flex:${n(s)}"></i>` : "").join("")}</div>
    <div class="cover-key">${Object.entries(STATUS).map(([s, [icon, label]]) =>
      `<span class="${s}"><b>${icon}</b> ${label} ${n(s)}</span>`).join("")}
      ${musts.length ? `<span class="musts">Must-haves ${musts.filter(r => r.status === "have").length}${
        musts.some(r => r.status === "bank") ? ` (+${musts.filter(r => r.status === "bank").length})` : ""} of ${musts.length}</span>` : ""}</div>
    <table class="reqs"><thead><tr><th></th><th>Requirement</th><th>Asked</th><th>Evidence</th></tr></thead>
      <tbody>${groups}</tbody></table></section>`;
}

// --- preparing and applying ------------------------------------------------------------------------

function actions(j) {
  const a = j.application;
  const submitted = a?.status === "submitted";
  const buttons = [];
  if (!a) buttons.push(`<button class="btn primary" data-act="prepare">Prepare application</button>`);
  else if (!submitted) buttons.push(`<button class="btn" data-act="again">Prepare again</button>`);
  if (j.fillable && a?.answers && !submitted && j.open) {
    buttons.push(`<button class="btn primary" data-act="apply">Apply…</button>`,
                 `<button class="btn" data-act="fill">Fill only, don't submit</button>`);
  }
  let hint = "";
  if (submitted) hint = "Submitted. Record what happens next under Progress.";
  else if (j.source === "mcf") hint = `MyCareersFuture takes applications only from Singapore citizens and PRs, but the job can be applied to elsewhere: on the company's own site, LinkedIn or by email. Look for it below, apply there (a prepared résumé is below to upload), then record it as Applied.`;
  else if (!j.fillable) hint = `trawl can't fill ${esc(BOARDS[j.source] || j.source)} forms: apply on the <a href="${esc(j.url)}" target="_blank">posting</a> (a prepared résumé is below to upload), then record it as Applied.`;
  else if (!a) hint = "Tailors your résumé and drafts the form (a DeepSeek call), so you can check both before applying.";
  else if (!a.answers) hint = "trawl couldn't read this form: apply on the posting and record it as Applied.";
  else hint = "Apply opens Chrome and fills the form; you finish and check it there, then approve here.";
  return `<section><h2>Apply</h2>
    ${buttons.length ? `<div class="actions" style="margin-top:0">${buttons.join("")}</div>` : ""}
    <div class="hint">${hint}</div><div id="task"></div>${submitted ? "" : picker(j)}${j.source === "mcf" && !submitted ? where(j) : ""}</section>`;
}

// Which projects go on the résumé: trawl's choice, or the person's
function picker(j) {
  const list = state.projects?.projects || [];
  if (list.length <= (state.projects?.most || 0)) return "";
  const now = j.application?.projects || [];
  return `<details class="picker"><summary>Choose the projects${
      now.length ? ` <span class="when">now: ${now.map(t => esc(t.split(":")[0])).join(", ")}${j.application.chosen ? " (your choice)" : ""}</span>` : ""}</summary>
    ${list.map(p => `<label><input type="checkbox" value="${esc(p.title)}" ${now.includes(p.title) ? "checked" : ""}>
      ${esc(p.title)}${p.spare ? ' <span class="tag">bank</span>' : ""}</label>`).join("")}
    <div class="actions"><button class="btn" data-act="pick">Prepare with these</button>
      <span class="when" id="picked"></span></div></details>`;
}

function watchPicker() {
  const box = document.querySelector(".picker");
  if (!box) return;
  const update = () => {
    const n = box.querySelectorAll("input:checked").length;
    box.querySelectorAll("input:not(:checked)").forEach(i => { i.disabled = n >= state.projects.most; });
    document.getElementById("picked").textContent = `${n} of up to ${state.projects.most}`;
  };
  box.onchange = update;
  update();
}

function where(j) {
  const leads = j.leads || [];
  const icon = { posting: "★", board: "▦", careers: "◎", website: "◎", search: "⌕" };
  return `<div class="where"><div class="where-head"><b>Where to apply</b>
      <button class="btn" id="find">${j.leads_at ? "Look again" : "Find where to apply"}</button>
      ${j.leads_at ? `<span class="when">looked ${fmtDate(j.leads_at)}</span>` : ""}</div>
    ${leads.length ? `<ul class="plain leads">${leads.map(l => `<li class="${l.kind}"><span class="ic">${icon[l.kind] || "•"}</span>
      <a href="${esc(l.url)}" target="_blank">${esc(l.label)}</a>
      ${l.fillable ? `<span class="tag" title="Add ${esc(l.board)}: ${esc(l.slug)} to sources.toml and trawl collects it, then fills it">trawl can fill once collected</span>` : ""}</li>`).join("")}</ul>`
      : `<div class="hint">Looks for the same job on the company's own board, its website and careers page, and gives LinkedIn and Google searches.</div>`}
  </div>`;
}

async function act(j, what, button) {
  const picked = [...document.querySelectorAll(".picker input:checked")].map(i => i.value);
  if (what === "pick" && !picked.length) return alert("Choose at least one project.");
  button.disabled = true;
  const [path, body] = { pick: ["prepare", { again: true, projects: picked }], prepare: ["prepare", {}], again: ["prepare", { again: true }],
                         apply: ["apply", { submit: true }], fill: ["apply", { submit: false }] }[what];
  try {
    const t = await api(`/api/jobs/${j.id}/${path}`, { method: "POST", body: JSON.stringify(body) });
    follow(t.id, j.id);
  } catch (e) { alert(e.message); button.disabled = false; }
}

// Follow a task until it ends, showing it while its job is open; a prompt waiting elsewhere shows in the nav
async function follow(taskId, jobId) {
  if (state.following[jobId] === taskId) return;
  state.following[jobId] = taskId;
  let seen = "";
  while (state.following[jobId] === taskId) {
    let t;
    try { t = await api(`/api/tasks/${taskId}`); } catch { break; }
    const now = JSON.stringify(t);
    if (now !== seen && state.selected === jobId) renderTask(t);
    seen = now;
    waiting(t.state === "waiting" && state.selected !== jobId ? jobId : null, jobId);
    if (t.state !== "running" && t.state !== "waiting") {
      delete state.following[jobId];
      state.last[jobId] = t;
      if (state.selected === jobId) await refresh(jobId);
      return;
    }
    await new Promise(r => setTimeout(r, 800));
  }
}

function waiting(jobId, forJob) {
  const foot = document.getElementById("nav-foot");
  if (jobId) foot.innerHTML = `<a href="#/jobs/${jobId}">● #${jobId} is waiting for you</a>`;
  else if (foot.dataset.job == forJob) foot.innerHTML = "";
  foot.dataset.job = jobId || "";
}

function renderTask(t) {
  const box = document.getElementById("task");
  if (!box) return;
  const kind = t.kind === "prepare" ? "Preparing" : "Applying";
  const status = { running: "working…", waiting: "waiting for you", done: "done", failed: "failed" }[t.state];
  box.innerHTML = `<div class="task ${t.state}">
    <div class="task-head">${kind} · ${status}</div>
    ${t.messages.length ? `<pre class="log">${esc(t.messages.join("\n"))}</pre>` : ""}
    ${t.prompt ? `<div class="prompt"><div>${esc(t.prompt.text)}</div><div class="actions">
      ${t.prompt.choices.map((c, i) => `<button class="btn ${i === 0 ? "primary" : ""}" data-reply="${esc(c.reply)}">${esc(c.label)}</button>`).join("")}
      </div></div>` : ""}
    ${t.error ? `<div class="task-error">${esc(t.error)}</div>` : ""}
  </div>`;
  const log = box.querySelector(".log");
  if (log) log.scrollTop = log.scrollHeight;
  const busy = t.state === "running" || t.state === "waiting";  // one task per job: the server would refuse
  document.querySelectorAll("[data-act]").forEach(x => { x.disabled = busy; });
  box.querySelectorAll("[data-reply]").forEach(b => b.onclick = async () => {
    box.querySelectorAll("[data-reply]").forEach(x => { x.disabled = true; });
    try { await api(`/api/tasks/${t.id}/reply`, { method: "POST", body: JSON.stringify({ reply: b.dataset.reply }) }); }
    catch (e) { alert(e.message); }
  });
}

function application(j) {
  const a = j.application;
  const groups = { needs_you: [], drafted: [], filled: [], skipped: [] };
  (a.answers || []).forEach(x => groups[x.status]?.push(x));
  const shown = x => Array.isArray(x.value) ? x.value.join(", ") : x.value === true ? "yes" : x.value === false ? "no"
    : typeof x.value === "string" ? x.value : `‹${x.source}›`;
  return `
    <section><h2>Application</h2>
      <div>Prepared ${fmtDate(a.created_at)}${a.submitted_at ? ` · submitted ${fmtDate(a.submitted_at)}` : ""}
        ${a.has_pdf ? ` · <a href="#" id="show-pdf" onclick="return false">Show résumé</a>` : ""}</div>
      <div id="pdf-box"></div>
      ${a.edits.length || a.changes.length ? `<ul class="plain" style="margin-top:8px">
        ${a.edits.map(e => `<li>Δ ${esc(e)}</li>`).join("")}${a.changes.map(c => `<li>• ${esc(c)}</li>`).join("")}
        ${a.notes.map(n => `<li class="warn">⚠ ${esc(n)}</li>`).join("")}</ul>` : ""}
    </section>
    ${a.answers ? `<section class="answers"><h2>Form · ${a.answers.length} questions</h2>
      ${groups.needs_you.length ? `<div class="group"><div class="needs">Needs you (${groups.needs_you.length})</div>
        <ul class="plain">${groups.needs_you.map(x => `<li><span class="q">${esc(x.question.label)}</span> <span class="needs">— ${esc(x.note)}</span></li>`).join("")}</ul></div>` : ""}
      ${groups.drafted.map(x => `<div class="group"><div class="q">✎ ${esc(x.question.label)}</div><div class="a">${esc(x.value)}</div>
        ${x.note ? `<div class="warn">⚠ ${esc(x.note)}</div>` : ""}</div>`).join("")}
      ${groups.filled.length ? `<div class="group"><div>Filled (${groups.filled.length})</div><ul class="plain">
        ${groups.filled.map(x => `<li><span class="q">${esc(x.question.label)}</span> → ${esc(shown(x))}</li>`).join("")}</ul></div>` : ""}
      ${groups.skipped.length ? `<div class="group" style="color:var(--faint)">Skipped: ${groups.skipped.map(x => esc(x.question.label)).join("; ")}</div>` : ""}
    </section>` : ""}`;
}

function summary(a) {
  const d = a.detail || {};
  switch (a.event) {
    case "filled": return `${d.done} filled${d.left?.length ? `, ${d.left.length} left` : ""}${d.failed?.length ? `, ${d.failed.length} failed` : ""}`;
    case "approved": return `${d.fields} fields`;
    case "submitted": return [d.confirmed_by && `confirmed by ${d.confirmed_by}`, d.via && d.via !== "trawl" && `via ${d.via}`, d.note].filter(Boolean).join(" · ");
    case "form_errors": return (d.errors || []).join("; ");
    case "consented": return (d.labels || []).join("; ");
    case "skipped": return d.reason || "";
    default: return Object.keys(d).length ? JSON.stringify(d) : "";
  }
}

async function refreshIfOpen(id) {  // a slow call may finish after another job was opened
  if (state.selected === id && document.querySelector(".jobs")) await refresh(id);
}

async function refresh(id) {
  state.jobs = await api("/api/jobs");
  renderRows();
  await showJob(id);
}

function lightbox(src) {
  const box = document.getElementById("lightbox");
  box.innerHTML = `<img src="${src}" alt="">`;
  box.hidden = false;
  box.onclick = () => { box.hidden = true; };
}
document.addEventListener("keydown", e => { if (e.key === "Escape") document.getElementById("lightbox").hidden = true; });

// --- stats --------------------------------------------------------------------------------------

async function showStats() {
  let s;
  try {
    s = await api("/api/stats");
  } catch (e) {
    $main.innerHTML = `<div class="error">Can't load stats: ${esc(e.message)}</div>`;
    return;
  }
  const f = s.funnel;
  // from the jobs worth applying to: the 1000-odd collected would flatten every other bar
  const steps = [["Eligible", f.eligible], ["Prepared", f.prepared], ["Applied", f.applied],
                 ["Interview", f.interview], ["Offer", f.offer]];
  const top = Math.max(1, f.eligible);
  const maxWeek = Math.max(1, ...s.weekly.map(w => w.applied));
  const maxSrc = Math.max(1, ...s.by_source.map(r => r.eligible));
  $main.innerHTML = `
    <div class="stats">
      <h1>Stats</h1>
      <div class="tiles">
        ${[["Jobs collected", f.collected], ["Eligible", f.eligible], ["Applied", f.applied], ["Interviews", f.interview], ["Offers", f.offer], ["Rejected", f.rejected]]
          .map(([k, v]) => `<div class="tile"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("")}
      </div>
      <div class="card"><h2>Funnel</h2>
        ${steps.map(([k, v], i) => `<div class="bar"><span>${k}</span>
          <div class="track"><div class="fill" style="width:${(100 * v / top).toFixed(1)}%"></div></div>
          <span class="num">${v}${i > 0 && steps[i - 1][1] ? ` · ${Math.round(100 * v / steps[i - 1][1])}%` : ""}</span></div>`).join("")}
      </div>
      <div class="card"><h2>Applied per week</h2>
        ${s.weekly.length ? `<div class="weeks">${s.weekly.map(w => `<div class="wk"><span class="cnt">${w.applied}</span>
          <div class="col" style="height:${(100 * w.applied / maxWeek).toFixed(0)}%"></div><span class="lbl">${fmtDate(w.week)}</span></div>`).join("")}</div>`
          : `<div style="color:var(--faint)">No applications yet.</div>`}
      </div>
      <div class="card"><h2>By board · eligible / applied</h2>
        ${s.by_source.map(r => `<div class="bar"><span>${esc(BOARDS[r.source] || r.source)}</span>
          <div class="track"><div class="fill" style="width:${(100 * r.eligible / maxSrc).toFixed(1)}%"></div></div>
          <span class="num">${r.eligible} / ${r.applied}</span></div>`).join("")}
      </div>
    </div>`;
}

route();
