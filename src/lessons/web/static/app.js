// lessons front-end (Pass C). Situation search, cited result cards, splash
// floating leaders, and per-browser install affordance (R§4b.8-10).

const $ = (sel) => document.querySelector(sel);

// ---------- search ----------
async function runSearch(q) {
  const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
  const data = await res.json();
  renderResults(data);
}

function citationLine(c) {
  // c is a PodcastCitation dict: host (artist) separate from leaders (subjects)
  const when = c.label || "youtu.be";
  const ep = c.episode_title ? ` — ${c.episode_title}` : "";
  return `
    <div class="card-cite">
      <a href="${c.deep_link}" target="_blank" rel="noopener">${when}</a>
      <span class="cite-host">${c.host || ""}${ep}</span>
    </div>`;
}

function cardHTML(p) {
  const c = (p.citations && p.citations[0]) || null;
  const leaders = c && c.leaders && c.leaders.length ? c.leaders.join(", ") : (p.label || "");
  const quote = c ? firstQuote(p) : (p.summary || p.label);
  const diverge = p.divergent_from && p.divergent_from.length
    ? `<p class="diverge-band">Contrasts with another leader's take on the same situation.</p>`
    : "";
  return `
    <article class="card">
      <div class="card-leader">${escapeHtml(leaders)}</div>
      <p class="card-quote">${escapeHtml(quote)}</p>
      ${diverge}
      ${c ? citationLine(c) : ""}
    </article>`;
}

function firstQuote(p) {
  // The quote travels on the mention; the API returns it inside node detail,
  // but for the list we show the label/summary as the headline and the
  // citation deep-links to the exact words. Keep it honest: show summary/label.
  return p.summary || p.label;
}

function renderResults(data) {
  $("#splash").hidden = true;
  $("#results").hidden = false;
  const list = $("#results-list");
  const empty = $("#results-empty");
  const meta = $("#results-meta");

  if (!data.pointers || data.pointers.length === 0) {
    list.innerHTML = "";
    empty.hidden = false;
    meta.textContent = `No matches for “${data.query}”.`;
    return;
  }
  empty.hidden = true;
  const matched = data.matched_situations && data.matched_situations.length
    ? ` · situation: ${data.matched_situations.join("; ")}` : "";
  meta.textContent = `${data.pointers.length} pointer(s)${matched}`;
  list.innerHTML = data.pointers.map(cardHTML).join("");
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[m]));
}

// ---------- splash floating leaders ----------
async function loadSplash() {
  try {
    const res = await fetch("/api/splash");
    const data = await res.json();
    const layer = $("#float-layer");
    const n = data.elements.length;
    data.elements.forEach((el, i) => {
      const chip = document.createElement("div");
      chip.className = "chip";
      chip.innerHTML = el.quote
        ? `${escapeHtml(el.label)} · <span class="chip-quote">“${escapeHtml(trunc(el.quote, 48))}”</span>`
        : escapeHtml(el.label);
      // scatter around the edges, away from the centered hero
      const angle = (i / n) * 2 * Math.PI;
      const top = 50 + 38 * Math.sin(angle);
      const left = 50 + 42 * Math.cos(angle);
      chip.style.top = `${Math.max(4, Math.min(92, top))}%`;
      chip.style.left = `${Math.max(2, Math.min(80, left))}%`;
      chip.style.animationDelay = `${(i % 6) * -2.5}s`;
      layer.appendChild(chip);
    });
  } catch (e) { /* splash is decorative; ignore failures */ }
}

function trunc(s, n) { return s.length > n ? s.slice(0, n - 1) + "…" : s; }

// ---------- per-browser install affordance (R§4b.8-10) ----------
let deferredPrompt = null;

window.addEventListener("beforeinstallprompt", (e) => {
  // Chromium path: capture the event and show the one-click button.
  e.preventDefault();
  deferredPrompt = e;
  const btn = $("#install-btn");
  btn.hidden = false;
  btn.addEventListener("click", async () => {
    btn.hidden = true;
    deferredPrompt.prompt();
    await deferredPrompt.userChoice;
    deferredPrompt = null;
  });
});

function setupInstallFallback() {
  // Fires only if beforeinstallprompt never did (Safari / Firefox / installed).
  setTimeout(() => {
    if (deferredPrompt || !$("#install-btn").hidden) return;
    const ua = navigator.userAgent;
    const hint = $("#install-hint");
    const isSafari = /^((?!chrome|android|crios|fxios).)*safari/i.test(ua);
    const isIOS = /iphone|ipad|ipod/i.test(ua);
    const isFirefox = /firefox|fxios/i.test(ua);
    if (isSafari || isIOS) {
      hint.textContent = isIOS
        ? "Install: Share → Add to Home Screen"
        : "Install: Share → Add to Dock";
      hint.hidden = false;
    } else if (isFirefox) {
      hint.textContent = "Install not available in Firefox — pin as a tab.";
      hint.hidden = false;
    }
    // Chromium-but-already-installed: show nothing (correct — no dead button).
  }, 1200);
}

// ---------- wiring ----------
$("#search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#search-input").value.trim();
  if (q) runSearch(q);
});
$("#back-btn").addEventListener("click", () => {
  $("#results").hidden = true;
  $("#splash").hidden = false;
  $("#search-input").value = "";
  $("#search-input").focus();
});

loadSplash();
setupInstallFallback();
