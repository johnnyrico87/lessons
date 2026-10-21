// Shared detail panel. Opened from the splash leaders and from the graph nodes.
// A docked RIGHT-SIDE RAIL (not a centered modal) so it stays open while you
// keep exploring the graph. Fetched from /api/node/{id}/full:
//   * header  — image + name + external bio summary (badged)
//   * key info — node summary + the timestamped quotes (deep links)
//   * facts    — lessons / principles / experiences / situations tied to the person/org
//   * connected leaders — clickable images of other people connected in the
//     transcript (aligned / in tension / shared experience / mentions)
//
// Clicking a connected leader loads THAT entity in the SAME rail, so you can hop
// Musk -> someone he aligns with -> their tensions, etc.

(function () {
  const KIND_LABEL = {
    tension: "In tension with",
    aligned: "Aligned with",
    shared: "Shared experience",
    mentions: "Mentions / mentioned by",
  };
  const KIND_COLOR = {
    tension: "var(--tension, #e0a458)",
    aligned: "var(--accent, #7aa2f7)",
    shared: "#7dcfff",
    mentions: "var(--text-dim, #9aa3b2)",
  };
  const FACT_LABEL = {
    lesson: "Lessons", principle: "Principles", experience: "Experiences",
    situation: "Situations", company: "Companies", era: "Eras",
  };

  function esc(s) {
    return String(s || "").replace(/[&<>"']/g, (m) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[m]));
  }
  function initials(name) {
    return String(name || "").split(/\s+/).map((w) => w[0]).filter(Boolean)
      .slice(0, 2).join("").toUpperCase();
  }
  function fmtTs(s) {
    s = parseInt(s || 0, 10);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
             : `${m}:${String(sec).padStart(2, "0")}`;
  }

  function ensureRail() {
    let rail = document.getElementById("detail-rail");
    if (rail) return rail;
    rail = document.createElement("aside");
    rail.id = "detail-rail";
    rail.className = "detail-rail";
    rail.setAttribute("aria-label", "Detail panel");
    rail.hidden = true;
    rail.innerHTML = `<button class="lc-close" aria-label="Close panel">×</button>
      <div class="lc-body"></div>`;
    document.body.appendChild(rail);
    rail.querySelector(".lc-close").addEventListener("click", closeCard);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !rail.hidden) closeCard();
    });
    return rail;
  }

  function closeCard() {
    const rail = document.getElementById("detail-rail");
    if (rail) {
      rail.classList.remove("open");
      // let the slide-out transition finish before hiding
      setTimeout(() => { if (!rail.classList.contains("open")) rail.hidden = true; }, 220);
    }
    document.body.classList.remove("rail-open");
  }

  function avatar(imageUrl, label, cls) {
    return imageUrl
      ? `<img class="${cls}" src="${esc(imageUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" />`
      : `<span class="${cls} lc-initials">${esc(initials(label))}</span>`;
  }

  function renderCard(d) {
    const img = (d.bio && d.bio.image_url) || null;

    // Header
    let html = `<header class="lc-head">
      ${avatar(img, d.label, "lc-avatar")}
      <div>
        <div class="lc-kind">${esc(d.type)}</div>
        <h2 class="lc-name">${esc(d.label)}</h2>
        ${d.summary ? `<p class="lc-summary">${esc(d.summary)}</p>` : ""}
      </div>
    </header>`;

    // External bio (badged)
    if (d.bio && d.bio.summary) {
      html += `<section class="lc-section">
        <span class="lc-badge">About · external</span>
        <p class="lc-bio">${esc(d.bio.summary)}</p>
        <a class="lc-src" href="${esc(d.bio.source_url)}" target="_blank" rel="noopener">${esc(d.bio.source)} · ${esc(d.bio.license)}</a>
      </section>`;
    }

    // Key quotes (timestamped deep links)
    const cites = d.citations || [];
    if (cites.length) {
      html += `<section class="lc-section"><h3 class="lc-h3">In their words</h3>`;
      cites.slice(0, 6).forEach((c) => {
        html += `<div class="lc-cite">
          <a href="${esc(c.deep_link)}" target="_blank" rel="noopener">▶ ${esc(fmtTs(c.timestamp_s))}</a>
          <span class="lc-cite-meta">${esc(c.host || "")}${c.episode_title ? " — " + esc(c.episode_title) : ""}</span>
        </div>`;
      });
      html += `</section>`;
    }

    // Facts grouped by concept type
    const facts = d.facts || [];
    if (facts.length) {
      const byType = {};
      facts.forEach((f) => { (byType[f.type] = byType[f.type] || []).push(f); });
      html += `<section class="lc-section"><h3 class="lc-h3">Captured from the transcripts</h3>`;
      Object.keys(FACT_LABEL).forEach((t) => {
        if (!byType[t]) return;
        html += `<div class="lc-facts"><div class="lc-facts-label">${FACT_LABEL[t]}</div>`;
        byType[t].forEach((f) => {
          const q = f.quote ? `<span class="lc-fact-quote">“${esc(f.quote)}”</span>` : "";
          html += `<div class="lc-fact"><span class="lc-fact-rel">${esc(f.relation)}</span> ${esc(f.label)} ${q}</div>`;
        });
        html += `</div>`;
      });
      html += `</section>`;
    }

    // Connected leaders — clickable images
    const conns = d.connections || [];
    if (conns.length) {
      const byKind = {};
      conns.forEach((c) => { (byKind[c.kind] = byKind[c.kind] || []).push(c); });
      html += `<section class="lc-section"><h3 class="lc-h3">Connected leaders</h3>`;
      ["tension", "aligned", "shared", "mentions"].forEach((k) => {
        if (!byKind[k]) return;
        html += `<div class="lc-conn-group"><div class="lc-conn-label" style="color:${KIND_COLOR[k]}">${KIND_LABEL[k]}</div><div class="lc-conn-row">`;
        byKind[k].forEach((c) => {
          const via = c.via ? ` title="via ${esc(c.via)}"` : "";
          html += `<button class="lc-conn" data-id="${esc(c.id)}"${via}>
            ${avatar(c.image_url, c.label, "lc-conn-img")}
            <span class="lc-conn-name">${esc(c.label)}</span>
          </button>`;
        });
        html += `</div></div>`;
      });
      html += `</section>`;
    } else if (d.type === "person") {
      html += `<section class="lc-section"><p class="lc-empty">No connected leaders captured yet — richer connections appear as more episodes are extracted.</p></section>`;
    }

    return html;
  }

  async function openCard(nodeId) {
    const rail = ensureRail();
    const body = rail.querySelector(".lc-body");
    body.innerHTML = `<p class="lc-loading">Loading…</p>`;
    rail.hidden = false;
    // force reflow so the slide-in transition runs even on first open
    void rail.offsetWidth;
    rail.classList.add("open");
    document.body.classList.add("rail-open");
    try {
      const d = await (await fetch(`/api/node/${encodeURIComponent(nodeId)}/full`)).json();
      body.innerHTML = renderCard(d);
      body.querySelectorAll(".lc-conn").forEach((btn) => {
        btn.addEventListener("click", () => openCard(btn.getAttribute("data-id")));
      });
      body.scrollTop = 0;
    } catch (e) {
      body.innerHTML = `<p class="lc-empty">Could not load this entity.</p>`;
    }
  }

  // Resolve a leader by label when we only have a name (splash chips) — the
  // splash API doesn't carry node ids, so look the id up via the graph node set.
  async function openCardByLabel(label) {
    try {
      const data = await (await fetch(`/api/graph?node_type=person`)).json();
      const match = (data.elements || []).find(
        (el) => el.data && el.data.label && el.data.label.toLowerCase() === String(label).toLowerCase()
      );
      if (match) return openCard(match.data.id);
    } catch (e) { /* fall through */ }
    return null;
  }

  window.LeaderCard = { open: openCard, openByLabel: openCardByLabel, close: closeCard };
})();
