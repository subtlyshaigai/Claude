"use strict";

// --------------------------------------------------------------------------
// Small helpers
// --------------------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])));
const fmtWhen = (iso) => (iso ? String(iso).replace("T", " ").replace("Z", "") : "");

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || (await res.json()).error || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

// --------------------------------------------------------------------------
// State
// --------------------------------------------------------------------------
const state = { userId: null, users: [], tab: "projects", data: null };

// --------------------------------------------------------------------------
// Status + users
// --------------------------------------------------------------------------
async function loadStatus() {
  try {
    const s = await api("/api/status");
    const pill = $("#statusPill");
    if (s.llm_enabled) {
      pill.className = "pill pill-ok";
      pill.textContent = `online · ${s.model}`;
    } else {
      pill.className = "pill pill-off";
      pill.textContent = "offline · no API key";
    }
  } catch (e) {
    $("#statusPill").textContent = "unreachable";
  }
}

async function loadUsers() {
  const { users } = await api("/api/users");
  state.users = users;
  if (!state.userId && users.length) state.userId = users[0].id;
  const sel = $("#userSelect");
  sel.innerHTML = "";
  users.forEach((u) => {
    const o = el("option");
    o.value = u.id;
    o.textContent = u.role === "principal" ? `${u.name} ★` : u.name;
    if (u.id === state.userId) o.selected = true;
    sel.appendChild(o);
  });
}

// --------------------------------------------------------------------------
// Chat
// --------------------------------------------------------------------------
function addMessage(who, text, actions) {
  const wrap = el("div", `msg ${who === "Aries" ? "aries" : "user"}`);
  wrap.appendChild(el("div", "who", esc(who)));
  const bubble = el("div", "bubble");
  bubble.innerHTML = renderText(text);
  wrap.appendChild(bubble);
  if (actions && actions.length) {
    const a = el("div", "actions", "Actions: ");
    actions.forEach((act) => a.appendChild(el("span", "chip", esc(act.tool))));
    wrap.appendChild(a);
  }
  $("#messages").appendChild(wrap);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  return wrap;
}

function renderText(t) {
  // minimal markdown: **bold**, `code`, and headings/lists preserved as text
  return esc(t)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

async function sendMessage(message) {
  addMessage(userName(), message);
  const thinking = addMessage("Aries", "…");
  thinking.querySelector(".bubble").classList.add("typing");
  try {
    const r = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, user_id: state.userId }),
    });
    thinking.remove();
    addMessage("Aries", r.reply, r.actions);
    if (r.actions && r.actions.length) loadDashboard();
  } catch (e) {
    thinking.remove();
    addMessage("Aries", "Communication fault: " + e.message);
  }
}

async function runBriefing(kind) {
  const thinking = addMessage("Aries", `Preparing ${kind} briefing…`);
  thinking.querySelector(".bubble").classList.add("typing");
  try {
    const r = await api(`/api/briefing/${kind}?user_id=${state.userId}`);
    thinking.remove();
    addMessage("Aries", r.reply);
  } catch (e) {
    thinking.remove();
    addMessage("Aries", "Could not prepare briefing: " + e.message);
  }
}

const userName = () => {
  const u = state.users.find((x) => x.id === state.userId);
  return u ? u.name : "You";
};

// --------------------------------------------------------------------------
// Dashboard
// --------------------------------------------------------------------------
async function loadDashboard() {
  try {
    state.data = await api("/api/dashboard");
  } catch (e) {
    $("#boardBody").innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`;
    return;
  }
  renderTab(state.tab);
}

function tag(text, kind) {
  return `<span class="tag ${kind || ""}">${esc(text)}</span>`;
}

function boardHead(title, addLabel, onAdd) {
  const head = el("div", "board-head");
  head.appendChild(el("h2", null, esc(title)));
  if (addLabel) {
    const b = el("button", "add-btn", esc(addLabel));
    b.onclick = onAdd;
    head.appendChild(b);
  }
  return head;
}

function card(inner) { return el("div", "card", inner); }
function empty(msg) { return el("div", "empty", esc(msg)); }

const RENDERERS = {
  projects() {
    const body = el("div");
    body.appendChild(boardHead("Projects & Businesses", "+ project", () => openForm("projects")));
    const items = state.data.projects || [];
    if (!items.length) { body.appendChild(empty("No projects yet. Ask Aries to track one, or add it here.")); return body; }
    items.forEach((p) => {
      const c = card(
        `<div class="row1"><span class="title">${esc(p.name)}</span>
         <span>${tag(p.priority, p.priority)} ${tag(p.status.replace(/_/g, " "), p.status)}</span></div>
         ${p.objective ? `<div class="sub">${esc(p.objective)}</div>` : ""}
         <div class="meta">${p.deadline ? "Due " + esc(fmtWhen(p.deadline)) + " · " : ""}${p.next_action ? "Next: " + esc(p.next_action) : ""}${p.blockers ? " · Blocker: " + esc(p.blockers) : ""}</div>`
      );
      const acts = el("div", "card-actions");
      acts.appendChild(mini("Advance status", () => cycleProjectStatus(p)));
      acts.appendChild(mini("Edit", () => openForm("projects", p)));
      c.appendChild(acts);
      body.appendChild(c);
    });
    return body;
  },

  tasks() {
    const body = el("div");
    body.appendChild(boardHead("Tasks & Action Items", "+ task", () => openForm("tasks")));
    const items = state.data.tasks || [];
    if (!items.length) { body.appendChild(empty("No open tasks.")); return body; }
    items.forEach((t) => {
      const overdue = t.due && t.due < new Date().toISOString();
      const c = card(
        `<div class="row1"><span class="title">${esc(t.title)}</span>
         <span>${tag(t.priority, t.priority)} ${tag(t.status, t.status)}</span></div>
         ${t.detail ? `<div class="sub">${esc(t.detail)}</div>` : ""}
         <div class="meta">${t.category} ${t.due ? "· due " + esc(fmtWhen(t.due)) + (overdue ? " ⚠ overdue" : "") : ""}</div>`
      );
      const acts = el("div", "card-actions");
      if (t.status !== "done") acts.appendChild(mini("✓ Done", () => patch("tasks", t.id, { status: "done" })));
      acts.appendChild(mini("Edit", () => openForm("tasks", t)));
      c.appendChild(acts);
      body.appendChild(c);
    });
    return body;
  },

  events() {
    const body = el("div");
    body.appendChild(boardHead("Calendar (14 days)", "+ event", () => openForm("events")));
    (state.data.conflicts || []).forEach((cf) => {
      body.appendChild(el("div", "conflict-banner", `⚠ Conflict: “${esc(cf.a)}” overlaps “${esc(cf.b)}” near ${esc(fmtWhen(cf.when))}`));
    });
    const items = state.data.events || [];
    if (!items.length) { body.appendChild(empty("No upcoming events.")); return body; }
    items.forEach((e) => {
      const c = card(
        `<div class="row1"><span class="title">${esc(e.title)}</span><span>${tag(e.kind, "status")}</span></div>
         <div class="meta">${esc(fmtWhen(e.starts_at))}${e.location ? " · " + esc(e.location) : ""}${e.prep_needed ? " · prep needed" : ""}</div>
         ${e.prep_notes ? `<div class="sub">${esc(e.prep_notes)}</div>` : ""}`
      );
      const acts = el("div", "card-actions");
      acts.appendChild(mini("Edit", () => openForm("events", e)));
      acts.appendChild(mini("Delete", () => deleteEvent(e.id)));
      c.appendChild(acts);
      body.appendChild(c);
    });
    return body;
  },

  commitments() {
    const body = el("div");
    body.appendChild(boardHead("Open Commitments", "+ commitment", () => openForm("commitments")));
    const items = state.data.commitments || [];
    if (!items.length) { body.appendChild(empty("No open commitments.")); return body; }
    items.forEach((c0) => {
      const c = card(
        `<div class="row1"><span class="title">${esc(c0.description)}</span></div>
         <div class="meta">${esc(c0.owed_by)} → ${esc(c0.owed_to || "—")}${c0.due ? " · due " + esc(fmtWhen(c0.due)) : ""}</div>`
      );
      const acts = el("div", "card-actions");
      acts.appendChild(mini("Kept", () => patch("commitments", c0.id, { status: "kept" })));
      acts.appendChild(mini("Missed", () => patch("commitments", c0.id, { status: "missed" })));
      c.appendChild(acts);
      body.appendChild(c);
    });
    return body;
  },

  decisions() {
    const body = el("div");
    body.appendChild(boardHead("Open Decisions", "+ decision", () => openForm("decisions")));
    const items = state.data.decisions || [];
    if (!items.length) { body.appendChild(empty("No open decisions.")); return body; }
    items.forEach((d) => {
      const c = card(
        `<div class="row1"><span class="title">${esc(d.title)}</span>${d.confidence ? tag("conf: " + d.confidence, "status") : ""}</div>
         ${d.objective ? `<div class="sub">${esc(d.objective)}</div>` : ""}
         ${d.recommendation ? `<div class="meta">Recommendation: ${esc(d.recommendation)}</div>` : ""}`
      );
      body.appendChild(c);
    });
    return body;
  },

  people() {
    const body = el("div");
    body.appendChild(boardHead("People & Relationships", "+ person", () => openForm("people")));
    const items = state.data.people || [];
    if (!items.length) { body.appendChild(empty("No people tracked.")); return body; }
    items.forEach((p) => {
      body.appendChild(card(
        `<div class="row1"><span class="title">${esc(p.name)}</span><span class="meta">${esc(p.relationship || "")}</span></div>
         ${p.context ? `<div class="sub">${esc(p.context)}</div>` : ""}
         <div class="meta">${p.follow_up_by ? "Follow up by " + esc(fmtWhen(p.follow_up_by)) : ""}${p.last_contact ? " · last contact " + esc(fmtWhen(p.last_contact)) : ""}</div>`
      ));
    });
    return body;
  },

  memory() {
    const body = el("div");
    body.appendChild(boardHead("Memory (goals · preferences · business)", "+ memory", () => openForm("memory")));
    const items = state.data.memory || [];
    if (!items.length) { body.appendChild(empty("No memory stored yet.")); return body; }
    items.forEach((m) => {
      body.appendChild(card(
        `<div class="row1"><span class="title">${esc(m.content)}</span><span>${tag(m.scope, "status")} ${tag(m.category, "status")}</span></div>`
      ));
    });
    return body;
  },

  orders() {
    const body = el("div");
    body.appendChild(boardHead("Standing Orders (authorized autonomy)", "+ order", () => openForm("standing_orders")));
    const items = state.data.standing_orders || [];
    if (!items.length) { body.appendChild(empty("No standing orders. Aries acts at Level 0–2 until you authorize more.")); return body; }
    items.forEach((o) => {
      body.appendChild(card(
        `<div class="row1"><span class="title">${esc(o.title)}</span>${tag("Level " + o.autonomy_level, "status")}</div>
         <div class="sub">${esc(o.instruction)}</div>`
      ));
    });
    return body;
  },

  activity() {
    const body = el("div");
    body.appendChild(boardHead("Activity Log", null));
    const items = state.data.recent_actions || [];
    if (!items.length) { body.appendChild(empty("No actions logged yet.")); return body; }
    items.forEach((a) => {
      body.appendChild(card(
        `<div class="row1"><span class="title">${esc(a.summary)}</span><span class="meta">${a.autonomy_level != null ? "L" + a.autonomy_level : ""}</span></div>
         <div class="meta">${esc(a.actor)} · ${esc(a.tool)} · ${esc(fmtWhen(a.created_at))}</div>`
      ));
    });
    return body;
  },
};

function mini(label, fn) {
  const b = el("button", "mini-btn", esc(label));
  b.onclick = fn;
  return b;
}

function renderTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
  const body = $("#boardBody");
  body.innerHTML = "";
  if (!state.data) { body.appendChild(empty("Loading…")); return; }
  body.appendChild((RENDERERS[tab] || RENDERERS.projects)());
}

// --------------------------------------------------------------------------
// Mutations
// --------------------------------------------------------------------------
async function patch(entity, id, data) {
  await api(`/api/${entity}/${id}`, { method: "PATCH", body: JSON.stringify(data) });
  loadDashboard();
}

const PROJECT_STATUS_CYCLE = ["on_track", "at_risk", "blocked", "stalled", "awaiting_principal", "complete"];
function cycleProjectStatus(p) {
  const i = PROJECT_STATUS_CYCLE.indexOf(p.status);
  const next = PROJECT_STATUS_CYCLE[(i + 1) % PROJECT_STATUS_CYCLE.length];
  patch("projects", p.id, { status: next });
}

async function deleteEvent(id) {
  if (!confirm("Delete this event? This is irreversible (confirmation gate).")) return;
  await api(`/api/events/${id}?confirmed=true`, { method: "DELETE" });
  loadDashboard();
}

// --------------------------------------------------------------------------
// Forms (create/edit) — schema-driven modal
// --------------------------------------------------------------------------
const FORMS = {
  projects: {
    title: "Project",
    fields: [
      ["name", "text", "Name"],
      ["kind", "select", "Kind", ["project", "business", "goal"]],
      ["objective", "text", "Objective"],
      ["outcome", "text", "Desired outcome"],
      ["priority", "select", "Priority", ["critical", "high", "medium", "low"]],
      ["status", "select", "Status", ["on_track", "at_risk", "blocked", "stalled", "overdue", "awaiting_principal", "awaiting_other", "complete", "shelved"]],
      ["deadline", "datetime-local", "Deadline"],
      ["next_action", "text", "Next action"],
      ["blockers", "text", "Blockers"],
      ["notes", "textarea", "Notes"],
    ],
  },
  tasks: {
    title: "Task",
    fields: [
      ["title", "text", "Title"],
      ["detail", "textarea", "Detail"],
      ["priority", "select", "Priority", ["critical", "high", "medium", "low"]],
      ["category", "select", "Category", ["general", "errand", "admin", "followup", "recurring", "prep"]],
      ["status", "select", "Status", ["open", "in_progress", "blocked", "done", "dropped"]],
      ["due", "datetime-local", "Due"],
    ],
  },
  events: {
    title: "Event",
    fields: [
      ["title", "text", "Title"],
      ["starts_at", "datetime-local", "Starts"],
      ["ends_at", "datetime-local", "Ends"],
      ["kind", "select", "Kind", ["meeting", "appointment", "travel", "focus", "personal"]],
      ["location", "text", "Location"],
      ["prep_needed", "select", "Prep needed", ["false", "true"]],
      ["prep_notes", "textarea", "Prep notes"],
      ["attendees", "text", "Attendees"],
    ],
  },
  commitments: {
    title: "Commitment",
    fields: [
      ["description", "text", "Description"],
      ["owed_by", "text", "Owed by"],
      ["owed_to", "text", "Owed to"],
      ["due", "datetime-local", "Due"],
    ],
  },
  decisions: {
    title: "Decision",
    fields: [
      ["title", "text", "Title"],
      ["objective", "text", "Objective"],
      ["options", "textarea", "Options"],
      ["recommendation", "textarea", "Recommendation"],
      ["confidence", "text", "Confidence"],
      ["rationale", "textarea", "Rationale / assumptions"],
    ],
  },
  people: {
    title: "Person",
    fields: [
      ["name", "text", "Name"],
      ["relationship", "text", "Relationship"],
      ["context", "textarea", "Context"],
      ["last_contact", "datetime-local", "Last contact"],
      ["follow_up_by", "datetime-local", "Follow up by"],
      ["notes", "textarea", "Notes"],
    ],
  },
  memory: {
    title: "Memory",
    endpoint: "memory",
    fields: [
      ["content", "textarea", "Content"],
      ["scope", "select", "Scope", ["short_term", "long_term", "permanent"]],
      ["category", "select", "Category", ["goal", "preference", "business", "decision", "person", "note"]],
    ],
  },
  standing_orders: {
    title: "Standing Order",
    endpoint: "standing_orders",
    fields: [
      ["title", "text", "Title"],
      ["instruction", "textarea", "Instruction"],
      ["autonomy_level", "select", "Autonomy level", ["0", "1", "2", "3", "4"]],
    ],
  },
};

function toLocalInput(iso) {
  if (!iso) return "";
  return String(iso).replace("Z", "").slice(0, 16);
}
function fromLocalInput(v) {
  if (!v) return null;
  return v.length === 16 ? v + ":00Z" : v;
}

function openForm(entity, existing) {
  const spec = FORMS[entity];
  if (!spec) return;
  $("#modalTitle").textContent = (existing ? "Edit " : "New ") + spec.title;
  const body = $("#modalBody");
  body.innerHTML = "";
  const inputs = {};
  spec.fields.forEach(([key, type, label, opts]) => {
    const wrap = el("label", null, esc(label));
    let input;
    if (type === "select") {
      input = el("select");
      (opts || []).forEach((o) => {
        const op = el("option");
        op.value = o; op.textContent = o;
        input.appendChild(op);
      });
    } else if (type === "textarea") {
      input = el("textarea");
      input.rows = 2;
    } else {
      input = el("input");
      input.type = type;
    }
    if (existing && existing[key] != null) {
      input.value = type === "datetime-local" ? toLocalInput(existing[key]) : existing[key];
    }
    wrap.appendChild(input);
    inputs[key] = { input, type };
    body.appendChild(wrap);
  });

  const save = el("button", "save", existing ? "Save changes" : "Create");
  save.onclick = async () => {
    const payload = {};
    for (const [key, { input, type }] of Object.entries(inputs)) {
      let v = input.value;
      if (v === "") continue;
      if (type === "datetime-local") v = fromLocalInput(v);
      if (key === "prep_needed") v = v === "true";
      if (key === "autonomy_level") v = parseInt(v, 10);
      payload[key] = v;
    }
    try {
      const endpoint = spec.endpoint || entity;
      if (existing && !spec.endpoint) {
        await api(`/api/${entity}/${existing.id}`, { method: "PATCH", body: JSON.stringify(payload) });
      } else {
        await api(`/api/${endpoint}`, { method: "POST", body: JSON.stringify(payload) });
      }
      closeModal();
      loadDashboard();
    } catch (e) {
      alert("Save failed: " + e.message);
    }
  };
  body.appendChild(save);
  $("#modal").classList.remove("hidden");
}

function closeModal() { $("#modal").classList.add("hidden"); }

// --------------------------------------------------------------------------
// Wire-up
// --------------------------------------------------------------------------
function init() {
  // chat
  const form = $("#chatForm");
  const input = $("#chatInput");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const msg = input.value.trim();
    if (!msg) return;
    input.value = "";
    input.style.height = "auto";
    sendMessage(msg);
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 160) + "px";
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });

  document.querySelectorAll(".brief-btn").forEach((b) =>
    b.addEventListener("click", () => runBriefing(b.dataset.brief))
  );
  document.querySelectorAll(".tab").forEach((t) =>
    t.addEventListener("click", () => renderTab(t.dataset.tab))
  );

  $("#userSelect").addEventListener("change", (e) => { state.userId = parseInt(e.target.value, 10); });
  $("#addUserBtn").addEventListener("click", async () => {
    const name = prompt("Family member's name:");
    if (!name) return;
    const role = confirm("Is this the Principal (primary owner)? OK = yes, Cancel = family member.") ? "principal" : "family";
    await api("/api/users", { method: "POST", body: JSON.stringify({ name, role }) });
    await loadUsers();
  });

  $("#modalClose").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });

  addMessage("Aries", "Aries online. Constellation01 at your disposal. I hold the operating picture; bring me in where your judgment is required. How shall we proceed?");
}

(async function boot() {
  init();
  await loadStatus();
  await loadUsers();
  await loadDashboard();
})();
