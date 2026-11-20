/* Abionic DocSearch — logique de l'interface. */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const KIND_LABEL = {
  pdf: "PDF", word: "DOC", excel: "XLS", ppt: "PPT",
  image: "IMG", text: "TXT", other: "FILE",
};

const S = {
  boot: null,
  view: "search",
  query: "",
  type: "all",
  sort: "relevance",
  results: [],
  folders: [],
  selected: -1,
  previewItem: null,
  previewToken: 0,
  previewTimer: null,
  searchTimer: null,
  searchToken: 0,
  scanning: false,
  pollTimer: null,
  cfg: { roots: [], exclude_dirs: [], exclude_exts: [], exclude_names: [], threads: 8 },
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtInt(n) {
  return new Intl.NumberFormat("fr-FR").format(Number(n) || 0);
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric" })
    + " à " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("err", isError);
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 3200);
}

function highlight(text, query) {
  const raw = esc(text);
  const terms = (query || "").split(/[^\p{L}\p{N}\-]+/u).filter((t) => t.length > 1);
  if (!terms.length) return raw;
  const pattern = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return raw.replace(pattern, "<mark>$1</mark>");
}

function typeTile(kind) {
  const k = KIND_LABEL[kind] ? kind : "other";
  return `<div class="type-icon type-${k}">${KIND_LABEL[k]}</div>`;
}

/* ------------------------------------------------------------------ init */

async function init() {
  wire();
  try {
    S.boot = await Bridge.call("bootstrap");
  } catch (err) {
    toast("Impossible de charger l'application : " + err.message, true);
    return;
  }
  S.cfg = S.boot.config || S.cfg;
  S.cfg.roots = S.cfg.roots || [];
  S.cfg.exclude_exts = S.cfg.exclude_exts || [];
  S.cfg.exclude_names = S.cfg.exclude_names || [];
  renderChip();
  renderHead();
  renderRecent();
  showEmpty(true, "search");
  renderRoots();
  fillSettings();
  renderIndex();
  renderAbout();

  if (!S.boot.stats.files) {
    nav("index");
  } else {
    $("#q").focus();
  }
}

function wire() {
  $$("[data-nav]").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.preventDefault();
      nav(el.dataset.nav);
    });
  });

  const input = $("#q");
  input.addEventListener("input", () => {
    S.query = input.value.trim();
    clearTimeout(S.searchTimer);
    S.searchTimer = setTimeout(runSearch, 130);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (S.selected >= 0) openItem(S.results[S.selected]);
      else runSearch();
    }
  });
  $("#btnSearch").addEventListener("click", () => { runSearch(); input.focus(); });

  $("#typeChips").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    $$("#typeChips .chip").forEach((c) => c.classList.toggle("active", c === chip));
    S.type = chip.dataset.type;
    runSearch();
  });
  $("#sort").addEventListener("change", () => { S.sort = $("#sort").value; runSearch(); });

  $("#results").addEventListener("click", (event) => {
    const folder = event.target.closest(".folder-row");
    if (folder) {
      const value = folder.dataset.name || "";
      $("#q").value = value;
      S.query = value;
      S.type = "all";
      $$("#typeChips .chip").forEach((c) => c.classList.toggle("active", c.dataset.type === "all"));
      runSearch();
      return;
    }
    const row = event.target.closest(".result");
    if (row) select(Number(row.dataset.i), false, false, 0);
  });
  $("#results").addEventListener("dblclick", (event) => {
    const row = event.target.closest(".result");
    if (row) openItem(S.results[Number(row.dataset.i)]);
  });
  $("#results").addEventListener("mouseover", (event) => {
    const row = event.target.closest(".result");
    const index = row ? Number(row.dataset.i) : -1;
    if (row && index !== S.selected) select(index, false, true);
  });
  $("#recentList").addEventListener("click", (event) => {
    const row = event.target.closest(".recent-item");
    if (!row) return;
    const item = S.boot.recent[Number(row.dataset.i)];
    if (item) {
      S.previewItem = null;
      $("#previewBody").dataset.path = "";
      previewFor(item);
    }
  });

  document.addEventListener("keydown", onKey);

  // index
  $("#btnAddRoot").addEventListener("click", addRoot);
  $("#btnScan").addEventListener("click", startScan);
  $("#btnCancelScan").addEventListener("click", cancelScan);
  $("#btnGoSearch").addEventListener("click", () => nav("search"));
  $("#btnStepSearch").addEventListener("click", () => nav("search"));
  $("#btnOpenData").addEventListener("click", async () => {
    const r = await Bridge.call("open_data_dir");
    if (!r.ok) toast(r.error || "Impossible d'ouvrir le dossier", true);
  });
  $("#btnClearCache").addEventListener("click", async () => {
    const r = await Bridge.call("clear_cache");
    toast(r.ok ? `Cache vidé (${r.freed})` : "Échec du nettoyage", !r.ok);
    refreshCache();
  });
  $("#roots").addEventListener("click", (event) => {
    const btn = event.target.closest(".root-del");
    if (!btn) return;
    S.cfg.roots.splice(Number(btn.dataset.i), 1);
    persistConfig();
    renderRoots();
    renderIndex();
  });
  $("#threads").addEventListener("change", () => {
    S.cfg.threads = Math.max(1, Math.min(32, Number($("#threads").value) || 8));
    persistConfig();
  });
  $("#excludes").addEventListener("change", () => {
    S.cfg.exclude_dirs = $("#excludes").value.split(",").map((s) => s.trim()).filter(Boolean);
    persistConfig();
  });
  $("#excludeExts").addEventListener("change", () => {
    S.cfg.exclude_exts = $("#excludeExts").value.split(",").map((s) => s.trim()).filter(Boolean);
    persistConfig();
    updateAdvancedSummary();
  });
  $("#excludeNames").addEventListener("change", () => {
    S.cfg.exclude_names = $("#excludeNames").value.split(",").map((s) => s.trim()).filter(Boolean);
    persistConfig();
    updateAdvancedSummary();
  });
}

/* ------------------------------------------------------------------- nav */

function nav(name) {
  S.view = name;
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.nav === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  if (name === "index") {
    renderIndex();
    refreshCache();
  }
  if (name === "search") {
    setTimeout(() => $("#q").focus(), 40);
  }
}

function renderChip() {
  const chip = $("#indexChip");
  const stats = S.boot?.stats || { files: 0 };
  chip.classList.remove("stale", "empty");
  if (!stats.files) {
    chip.classList.add("empty");
    $("#indexChipText").textContent = "Aucun index";
  } else {
    $("#indexChipText").textContent = `${fmtInt(stats.files)} documents`;
    if (stats.last_scan) {
      const age = (Date.now() - new Date(stats.last_scan).getTime()) / 86400000;
      if (age > 7) chip.classList.add("stale");
    }
  }
}

/* --------------------------------------------------------------- recherche */

async function runSearch() {
  const token = ++S.searchToken;
  if (S.query.length < 2 && S.type === "all") {
    S.results = [];
    S.folders = [];
    S.selected = -1;
    $("#results").innerHTML = "";
    renderHead();
    showEmpty(true, "search");
    return;
  }
  const data = await Bridge.call(
    "search", S.query, S.type === "all" ? [] : [S.type], S.sort, 200
  );
  if (token !== S.searchToken) return;
  if (data.error) {
    toast("Recherche impossible : " + data.error, true);
    return;
  }
  S.results = data.files || [];
  S.folders = data.folders || [];
  S.selected = -1;
  renderResults(data);
  renderHead(data);
  if (S.results.length) select(0, false, false, 320);
}

function renderHead(data) {
  const head = $("#resultsHead");
  if (!data) {
    const total = S.boot?.stats?.files || 0;
    const dirs = S.boot?.stats?.dirs || 0;
    head.innerHTML = total
      ? `<span>Index local : <strong>${fmtInt(total)}</strong> documents · <strong>${fmtInt(dirs)}</strong> dossiers</span>`
      : `<span>Aucun document indexé — ouvrez l'onglet <strong>Index</strong> pour commencer.</span>`;
    return;
  }
  const count = S.results.length;
  head.innerHTML = `<strong>${fmtInt(count)}</strong> document${count > 1 ? "s" : ""}`
    + (S.folders.length ? `<span class="dot"></span><strong>${fmtInt(S.folders.length)}</strong> dossier${S.folders.length > 1 ? "s" : ""}` : "")
    + `<span class="dot"></span>${data.ms} ms`
    + `<span class="spacer"></span><span class="index-summary">trié par ${esc($("#sort").selectedOptions[0].textContent.toLowerCase())}</span>`;
}

function renderResults(data) {
  const box = $("#results");
  let html = "";

  if (S.folders.length) {
    html += `<div class="section-label">Dossiers</div>`;
    S.folders.forEach((folder) => {
      html += `
        <div class="folder-row" data-name="${esc(folder.name)}">
          <div class="type-icon type-folder"><svg><use href="#i-folder"/></svg></div>
          <div>
            <div class="folder-name">${highlight(folder.name, S.query)}</div>
            <div class="folder-path">${esc(folder.path)}</div>
          </div>
          <svg class="go"><use href="#i-arrow"/></svg>
        </div>`;
    });
    if (S.results.length) html += `<div class="section-label">Documents</div>`;
  }

  S.results.forEach((item, index) => {
    html += `
      <div class="result" data-i="${index}">
        ${typeTile(item.kind)}
        <div class="result-text">
          <div class="result-name">${highlight(item.name, S.query)}</div>
          <div class="result-path">${esc(item.folder)}</div>
        </div>
        <div class="result-meta">
          <div class="size">${esc(item.size_human)}</div>
          <div class="date">${esc(item.date)}</div>
        </div>
      </div>`;
  });

  box.innerHTML = html;
  showEmpty(S.results.length === 0 && S.folders.length === 0, "noresult");
}

function showEmpty(show, mode = "search") {
  const el = $("#emptyState");
  el.classList.toggle("show", show);
  if (!show) return;
  if (mode === "noresult") {
    $("#emptyTitle").textContent = S.query ? `Rien pour « ${S.query} »` : "Rien dans cette catégorie";
    $("#emptyText").innerHTML = "Essayez un mot plus court, un nom de dossier,<br>ou retirez le filtre de type.";
    $("#recentBlock").hidden = true;
  } else {
    $("#emptyTitle").textContent = "Cherchez dans tout le partage";
    $("#emptyText").innerHTML = "Tapez quelques lettres pour retrouver un document.<br>Les résultats restent dans l'application.";
    $("#recentBlock").hidden = !(S.boot?.recent?.length > 0);
  }
}

function renderRecent() {
  const list = $("#recentList");
  const recent = S.boot?.recent || [];
  if (!recent.length) {
    $("#recentBlock").hidden = true;
    return;
  }
  list.innerHTML = recent.map((item, index) => `
    <button class="recent-item" data-i="${index}">
      ${typeTile(item.kind)}
      <span class="name">${esc(item.name)}</span>
      <span class="date">${esc(item.date)}</span>
    </button>`).join("");
  $("#recentBlock").hidden = false;
}

function select(index, openPreview = false, fromHover = false, delay = null) {
  if (index < 0 || index >= S.results.length) return;
  S.selected = index;
  $$("#results .result").forEach((row) => {
    const active = Number(row.dataset.i) === index;
    row.classList.toggle("active", active);
    if (active && !fromHover) row.scrollIntoView({ block: "nearest" });
  });
  const item = S.results[index];
  // Les informations et les boutons apparaissent dès la sélection,
  // avant même le début du rendu de l'aperçu.
  if (S.previewItem?.path !== item.path) {
    renderPreviewDoc(item);
    renderActions(item);
    renderSkeleton();
    $("#previewFlag").textContent = "préparation…";
  }
  const wait = delay === null ? (fromHover ? 130 : 0) : delay;
  clearTimeout(S.previewTimer);
  S.previewTimer = setTimeout(() => previewFor(item), wait);
}

/* ---------------------------------------------------------------- aperçu */

function previewFacts(item) {
  return `<div class="preview-facts">
      <span class="fact">${esc(item.size_human)}</span>
      ${item.date ? `<span class="fact">${esc(item.date)}</span>` : ""}
    </div>`;
}

function renderPreviewDoc(item) {
  $("#previewDoc").innerHTML = `
    <div class="preview-name">${esc(item.name)}</div>
    <div class="preview-sub">${esc(item.folder)}</div>
    ${previewFacts(item)}`;
}

function renderActions(item) {
  const actions = $("#previewActions");
  actions.innerHTML = `
    <button class="btn primary small" id="pOpen"><svg><use href="#i-external"/></svg> Ouvrir</button>
    <button class="btn ghost small" id="pReveal"><svg><use href="#i-folder"/></svg> Dossier</button>
    <button class="btn ghost small icon-btn" id="pCopy" title="Copier le chemin"><svg><use href="#i-copy"/></svg></button>`;
  $("#pOpen").addEventListener("click", () => openItem(item));
  $("#pReveal").addEventListener("click", async () => {
    const r = await Bridge.call("reveal", item.path);
    if (!r.ok) toast(r.error || "Dossier introuvable", true);
  });
  $("#pCopy").addEventListener("click", async () => {
    const r = await Bridge.call("copy_path", item.path);
    toast(r.ok ? "Chemin copié" : "Copie impossible", !r.ok);
  });
}

function renderSkeleton() {
  $("#previewBody").innerHTML = `
    <div class="preview-loading">
      <div class="skel skel-page"></div>
      <div class="skel skel-line"></div>
      <div class="skel skel-line short"></div>
    </div>`;
}

async function previewFor(item) {
  if (!item) return;
  const body = $("#previewBody");
  if (S.previewItem?.path === item.path && body.dataset.path === item.path && !body.dataset.failed) {
    return;
  }
  S.previewItem = item;
  const token = ++S.previewToken;

  // Les infos et les boutons sont affichés avant tout rendu.
  renderPreviewDoc(item);
  renderActions(item);
  body.dataset.path = item.path;
  body.dataset.failed = "";
  renderSkeleton();
  $("#previewFlag").textContent = "préparation…";

  let fast;
  try {
    fast = await Bridge.call("preview_fast", item.path);
  } catch (err) {
    fast = { ok: false, error: err.message };
  }
  if (token !== S.previewToken) return;

  if (!fast || !fast.ok) {
    let full;
    try {
      full = await Bridge.call("preview", item.path, "full");
    } catch (err) {
      full = { ok: false, error: err.message };
    }
    if (token !== S.previewToken) return;
    if (full && full.ok) renderPreviewBody(full, item, "full");
    else renderPreviewError(full);
    return;
  }

  renderPreviewBody(fast, item, "fast");

  // Word / Excel / PowerPoint : le texte arrive tout de suite, la vraie page suit.
  const isOffice = ["word", "excel", "ppt"].includes(item.kind);
  if (fast.kind === "text" && isOffice && S.boot?.office) {
    $("#previewFlag").innerHTML = `texte immédiat · page réelle en cours…`;
    let full;
    try {
      full = await Bridge.call("preview", item.path, "full");
    } catch (err) {
      full = null;
    }
    if (token !== S.previewToken) return;
    if (full && full.ok) renderPreviewBody(full, item, "full");
    else $("#previewFlag").textContent = "aperçu texte";
  }
}

function renderPreviewBody(result, item) {
  const body = $("#previewBody");
  body.dataset.path = item.path;

  if (result.kind === "image") {
    body.innerHTML = `
      <div class="paper-frame">
        <img class="paper-img" alt="Première page de ${esc(item.name)}"
             src="data:image/jpeg;base64,${result.data}">
      </div>`;
    const src = result.source === "pdf" ? "PDF" : result.source === "image" ? "Image" : "Office";
    $("#previewFlag").innerHTML = `${result.cached ? '<span class="spark">●</span> ' : ""}page 1 · ${src} · ${result.ms} ms`;
    return;
  }

  const table = (result.table || []).length
    ? `<table class="paper-table">${result.table.map((row) => `<tr>${row.map((cell) => `<td>${esc(cell)}</td>`).join("")}</tr>`).join("")}</table>`
    : "";
  const text = result.text ? `<p class="paper-text">${esc(result.text)}</p>` : "";
  body.innerHTML = `
    <div class="paper-frame">
      <div class="paper">
        <div class="paper-title">${esc(result.title || item.name)}</div>
        <div class="paper-rule"></div>
        ${text}${table}
        ${result.note ? `<div class="paper-note">${esc(result.note)}</div>` : ""}
        <div class="paper-sheetmeta"><span>Abionic · DocSearch</span><span>Page 1</span></div>
      </div>
    </div>`;
  $("#previewFlag").textContent = result.note ? "aperçu texte" : `texte · ${result.ms} ms`;
}

function renderPreviewError(result) {
  const body = $("#previewBody");
  body.dataset.failed = "1";
  body.innerHTML = `
    <div class="preview-error">
      <svg><use href="#i-doc"/></svg>
      <div>${esc(result?.error || "Aperçu indisponible pour ce document.")}</div>
      <div style="margin-top:6px;font-size:10.5px">Vous pouvez quand même l'ouvrir avec le bouton ci-dessous.</div>
    </div>`;
  $("#previewFlag").textContent = "";
}

async function openItem(item) {
  if (!item) return;
  const r = await Bridge.call("open_file", item.path);
  if (!r.ok) toast(r.error || "Impossible d'ouvrir le document", true);
  else if (r.note) toast(r.note);
}

/* ------------------------------------------------------------------ index */

function renderIndex() {
  if (!S.boot) return;
  const stats = S.boot.stats;

  $("#statLine").innerHTML = [
    `<span class="mini-stat"><strong>${fmtInt(stats.files)}</strong> documents</span>`,
    `<span class="mini-stat"><strong>${fmtInt(stats.dirs)}</strong> dossiers</span>`,
    `<span class="mini-stat"><strong>${esc(stats.size_human || "0 o")}</strong></span>`,
    `<span class="mini-stat">${stats.last_scan ? "analysé le " + esc(fmtDate(stats.last_scan).split(" à ")[0]) : "jamais analysé"}</span>`,
  ].join("");

  const step = $("#nextStep");
  const text = $("#nextStepText");
  step.classList.remove("warn", "ready");
  if (stats.files) {
    step.classList.add("ready");
    text.innerHTML = "<strong>Votre index est prêt.</strong> Ouvrez l'onglet « Rechercher » et tapez le nom d'un document.";
  } else if (!S.cfg.roots.length) {
    step.classList.add("warn");
    text.innerHTML = "<strong>Commencez ici :</strong> cliquez sur « Ajouter un dossier » pour choisir l'endroit où se trouvent vos documents.";
  } else {
    step.classList.add("ready");
    text.innerHTML = "<strong>Tout est prêt.</strong> Cliquez sur « Analyser maintenant » : l'opération ne modifie rien et prend quelques secondes.";
  }

  const hasRoots = S.cfg.roots.length > 0;
  $("#btnScan").disabled = !hasRoots;
  const count = S.cfg.roots.length;
  $("#scanHint").textContent = hasRoots
    ? (count === 1 ? "1 dossier prêt. L'analyse ne modifie rien."
                   : `${fmtInt(count)} dossiers prêts. L'analyse ne modifie rien.`)
    : "Ajoutez d'abord un dossier à l'étape 1.";
  updateAdvancedSummary();
}

function updateAdvancedSummary() {
  const sub = document.querySelector("#advanced .adv-sub");
  if (!sub) return;
  const exts = (S.cfg.exclude_exts || []).length;
  const names = (S.cfg.exclude_names || []).length;
  sub.textContent = `${fmtInt(exts)} extensions et ${fmtInt(names)} noms ignorés · dossiers exclus · cache · dossier de données`;
}

function renderRoots() {
  const box = $("#roots");
  if (!S.cfg.roots.length) {
    box.innerHTML = `<div class="roots-empty">Aucun dossier choisi pour l'instant.<br>Utilisez le bouton ci-dessus.</div>`;
    return;
  }
  box.innerHTML = S.cfg.roots.map((root, index) => `
    <div class="root-row">
      <div class="type-icon type-folder"><svg><use href="#i-folder"/></svg></div>
      <div class="root-info">
        <div class="root-path">${esc(root)}</div>
        <div class="root-state" data-check="${index}">Vérification…</div>
      </div>
      <button class="root-del" data-i="${index}" title="Retirer"><svg><use href="#i-x"/></svg></button>
    </div>`).join("");

  Bridge.call("check_paths", S.cfg.roots).then((states) => {
    (states || []).forEach((state, index) => {
      const el = box.querySelector(`[data-check="${index}"]`);
      if (!el) return;
      el.textContent = state.exists ? "Accessible" : "Introuvable — lecteur non connecté ?";
      el.classList.toggle("bad", !state.exists);
    });
  });
}

function fillSettings() {
  $("#threads").value = S.cfg.threads || 8;
  $("#excludes").value = (S.cfg.exclude_dirs || []).join(", ");
  $("#excludeExts").value = (S.cfg.exclude_exts || []).join(", ");
  $("#excludeNames").value = (S.cfg.exclude_names || []).join(", ");
}

async function addRoot() {
  const folders = await Bridge.call("choose_folders");
  if (!folders || !folders.length) return;
  const known = new Set(S.cfg.roots.map((r) => r.toLowerCase()));
  folders.forEach((folder) => {
    if (!known.has(folder.toLowerCase())) S.cfg.roots.push(folder);
  });
  await persistConfig();
  renderRoots();
  renderIndex();
}

async function persistConfig() {
  try {
    await Bridge.call("save_config", {
      roots: S.cfg.roots,
      exclude_dirs: S.cfg.exclude_dirs,
      exclude_exts: S.cfg.exclude_exts,
      exclude_names: S.cfg.exclude_names,
      threads: S.cfg.threads,
    });
  } catch (err) {
    toast("Réglages non enregistrés : " + err.message, true);
  }
}

async function startScan() {
  if (!S.cfg.roots.length) {
    toast("Ajoutez d'abord un dossier à analyser", true);
    return;
  }
  const r = await Bridge.call("start_scan", S.cfg.roots);
  if (!r.ok) {
    toast(r.error || "Analyse impossible", true);
    return;
  }
  S.scanning = true;
  $("#scanIdle").hidden = true;
  $("#scanDone").hidden = true;
  $("#scanRun").hidden = false;
  $("#btnScan").disabled = true;
  $("#scanCurrent").textContent = "Préparation…";
  pollScan();
  S.pollTimer = setInterval(pollScan, 500);
}

async function pollScan() {
  const st = await Bridge.call("scan_status");
  $("#scanFiles").textContent = fmtInt(st.files);
  $("#scanDirs").textContent = fmtInt(st.dirs);
  $("#scanTime").textContent = `${Math.round(st.elapsed)} s`;
  if (st.current) {
    const text = String(st.current).replace(/^.*?([\\/][^\\/]+[\\/][^\\/]+)$/, "…$1");
    $("#scanCurrent").textContent = text;
  }
  if (!st.running && S.scanning) {
    S.scanning = false;
    clearInterval(S.pollTimer);
    $("#btnScan").disabled = false;
    $("#scanRun").hidden = true;
    $("#scanDone").hidden = false;
    if (st.error) {
      $("#doneTitle").textContent = "Analyse interrompue";
      $("#doneText").textContent = st.error;
      toast("Analyse interrompue : " + st.error, true);
    } else {
      const res = st.result || {};
      $("#doneTitle").textContent = res.cancelled ? "Analyse arrêtée" : "Analyse terminée";
      const ignored = res.ignored ? ` · ${fmtInt(res.ignored)} fichiers ignorés` : "";
      $("#doneText").innerHTML = res.cancelled
        ? `${fmtInt(res.files || 0)} documents dans l'index. Vous pourrez relancer l'analyse plus tard.`
        : `${fmtInt(res.files || 0)} documents · ${fmtInt(res.dirs || 0)} dossiers · ${res.elapsed || 0} s${ignored}`;
      toast(res.cancelled ? "Analyse arrêtée" : "Index à jour");
      await refreshBoot();
    }
  }
}

async function cancelScan() {
  await Bridge.call("cancel_scan");
  $("#scanCurrent").textContent = "Arrêt en cours…";
}

async function refreshCache() {
  const info = await Bridge.call("cache_info");
  $("#cacheText").textContent = `${info.human} utilisés par les aperçus déjà vus. Le cache se remplit au survol.`;
}

async function refreshBoot() {
  S.boot = await Bridge.call("bootstrap");
  S.cfg = S.boot.config || S.cfg;
  renderChip();
  renderHead();
  renderRecent();
  renderRoots();
  renderIndex();
}

function renderAbout() {
  $("#about").textContent =
    `Abionic DocSearch ${S.boot?.version || ""} — index local, aucune donnée envoyée.`;
}

/* -------------------------------------------------------------- clavier */

function onKey(event) {
  const input = $("#q");
  const inInput = document.activeElement === input;

  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    nav("search");
    input.focus();
    input.select();
    return;
  }
  if (event.key === "Escape") {
    if (inInput && input.value) {
      input.value = "";
      S.query = "";
      runSearch();
    } else {
      nav("search");
    }
    return;
  }
  if (S.view !== "search") return;

  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    if (!S.results.length) return;
    event.preventDefault();
    const delta = event.key === "ArrowDown" ? 1 : -1;
    const next = Math.max(0, Math.min(S.results.length - 1, S.selected + delta));
    select(next, false, false, 0);
    return;
  }
  if (event.key === "Enter" && S.selected >= 0 && !inInput) {
    event.preventDefault();
    openItem(S.results[S.selected]);
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c"
      && S.selected >= 0 && !inInput && !window.getSelection().toString()) {
    const item = S.results[S.selected];
    Bridge.call("copy_path", item.path).then((r) => toast(r.ok ? "Chemin copié" : "Copie impossible", !r.ok));
  }
}

document.addEventListener("DOMContentLoaded", init);
