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
  cfg: { roots: [], exclude_dirs: [], threads: 8 },
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

function typeTile(kind, small = false) {
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
  renderChip();
  renderSearchMeta();
  renderRecent();
  showEmpty(true, "search");
  renderRoots();
  fillSettings();
  renderAbout();

  if (!S.boot.stats.files) {
    nav("index");
    $("#scanRootsCount").textContent = fmtInt(S.cfg.roots.length);
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
    const row = event.target.closest(".result");
    if (row) select(Number(row.dataset.i), true);
    const folder = event.target.closest(".folder-row");
    if (folder) {
      const input = $("#q");
      const name = folder.dataset.name || "";
      input.value = name;
      S.query = name;
      S.type = "all";
      $$("#typeChips .chip").forEach((c) => c.classList.toggle("active", c.dataset.type === "all"));
      runSearch();
    }
  });
  $("#results").addEventListener("dblclick", (event) => {
    const row = event.target.closest(".result");
    if (row) openItem(S.results[Number(row.dataset.i)]);
  });
  $("#recentList").addEventListener("click", (event) => {
    const row = event.target.closest(".recent-item");
    if (!row) return;
    const item = S.boot.recent[Number(row.dataset.i)];
    if (item) {
      S.previewItem = item;
      previewFor(item, 0);
    }
  });

  document.addEventListener("keydown", onKey);

  // index
  $("#btnAddRoot").addEventListener("click", addRoot);
  $("#btnScan").addEventListener("click", startScan);
  $("#btnCancelScan").addEventListener("click", cancelScan);
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
  });
  $("#threads").addEventListener("change", () => {
    S.cfg.threads = Math.max(1, Math.min(32, Number($("#threads").value) || 8));
    persistConfig();
  });
  $("#excludes").addEventListener("change", () => {
    S.cfg.exclude_dirs = $("#excludes").value.split(",").map((s) => s.trim()).filter(Boolean);
    persistConfig();
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
    $("#resultsHead").innerHTML = "";
    showEmpty(true, "search");
    renderSearchMeta();
    return;
  }
  const data = await Bridge.call(
    "search", S.query, S.type === "all" ? [] : [S.type], S.sort, 150
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
  renderSearchMeta(data);
  if (S.results.length) select(0, false);
}

function renderSearchMeta(data) {
  const meta = $("#searchMeta");
  if (!data) {
    const total = S.boot?.stats?.files || 0;
    meta.innerHTML = total
      ? `Index local : <strong>${fmtInt(total)}</strong> documents et <strong>${fmtInt(S.boot.stats.dirs)}</strong> dossiers`
      : "Aucun document indexé pour l'instant";
    return;
  }
  const parts = [`<strong>${fmtInt(S.results.length)}</strong> résultat${S.results.length > 1 ? "s" : ""}`];
  if (S.folders.length) parts.push(`<strong>${fmtInt(S.folders.length)}</strong> dossier${S.folders.length > 1 ? "s" : ""}`);
  parts.push(`${data.ms} ms`);
  meta.innerHTML = parts.join(" · ");
}

function renderResults(data) {
  const head = $("#resultsHead");
  const box = $("#results");
  const count = S.results.length;
  head.innerHTML = count
    ? `<strong>${fmtInt(count)}</strong> document${count > 1 ? "s" : ""}<span class="dot"></span>trié par ${esc($("#sort").selectedOptions[0].textContent.toLowerCase())}`
    : "";

  let html = "";
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
          <svg class="go" style="width:16px;height:16px"><use href="#i-arrow"/></svg>
        </div>`;
    });
  }

  box.innerHTML = html;
  box.querySelectorAll(".result").forEach((row) => {
    row.addEventListener("mouseenter", () => {
      select(Number(row.dataset.i), false, true);
    });
  });
  showEmpty(count === 0 && S.folders.length === 0, "noresult");
}

function showEmpty(show, mode = "search") {
  const el = $("#emptyState");
  el.classList.toggle("show", show);
  if (!show) return;
  if (mode === "noresult") {
    $("#emptyTitle").textContent = S.query ? `Rien pour « ${S.query} »` : "Rien dans cette catégorie";
    $("#emptyText").innerHTML = "Essaie un mot plus court, un nom de dossier,<br>ou retire le filtre de type.";
    $("#recentBlock").hidden = true;
  } else {
    $("#emptyTitle").textContent = "Cherche dans tout le partage";
    $("#emptyText").innerHTML = "Tape quelques lettres pour retrouver un document.<br>Les résultats restent dans l'application, aucun navigateur.";
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

function select(index, openPreview = false, fromHover = false) {
  if (index < 0 || index >= S.results.length) return;
  S.selected = index;
  $$("#results .result").forEach((row) => {
    const active = Number(row.dataset.i) === index;
    row.classList.toggle("active", active);
    if (active) row.scrollIntoView({ block: "nearest" });
  });
  const item = S.results[index];
  schedulePreview(item, fromHover ? 240 : 40);
}

function schedulePreview(item, delay) {
  clearTimeout(S.previewTimer);
  S.previewTimer = setTimeout(() => previewFor(item, 0), delay);
}

/* ---------------------------------------------------------------- aperçu */

async function previewFor(item) {
  if (!item) return;
  if (S.previewItem && S.previewItem.path === item.path
      && $("#previewBody").dataset.path === item.path
      && !$("#previewBody").dataset.failed) {
    return;
  }
  S.previewItem = item;
  const token = ++S.previewToken;
  renderPreviewLoading(item);

  let result;
  try {
    result = await Bridge.call("preview", item.path);
  } catch (err) {
    result = { ok: false, error: err.message };
  }
  if (token !== S.previewToken) return;
  renderPreview(result, item);
}

function renderPreviewLoading(item) {
  const body = $("#previewBody");
  body.dataset.path = item.path;
  body.dataset.failed = "";
  body.innerHTML = `
    <div class="preview-loading">
      <div class="skel skel-page"></div>
      <div class="skel skel-line"></div>
      <div class="skel skel-line short"></div>
    </div>`;
  $("#previewFlag").textContent = "Première page…";
  $("#previewActions").innerHTML = "";
}

function renderPreview(result, item) {
  const body = $("#previewBody");
  const actions = $("#previewActions");
  body.dataset.path = item.path;
  const factSize = `<span class="fact">${esc(item.size_human)}</span>`;
  const factDate = item.date ? `<span class="fact">${esc(item.date)}</span>` : "";
  const facts = `<div class="preview-facts">${factSize}${factDate}</div>`;

  if (!result || !result.ok) {
    body.dataset.failed = "1";
    body.innerHTML = `
      <div class="preview-error">
        <svg><use href="#i-doc"/></svg>
        <div>${esc(result?.error || "Aperçu indisponible")}</div>
        <div style="margin-top:6px;font-size:10.5px">Le document peut quand même être ouvert.</div>
      </div>`;
    $("#previewFlag").textContent = "";
  } else if (result.kind === "image") {
    body.innerHTML = `
      <div class="paper-frame">
        <img class="paper-img" alt="Première page de ${esc(item.name)}"
             src="data:image/jpeg;base64,${result.data}">
      </div>
      <div class="preview-meta">
        <div class="preview-name">${esc(item.name)}</div>
        <div class="preview-sub">${esc(item.folder)}</div>
        ${facts}
      </div>`;
    const src = result.source === "pdf" ? "PDF" : result.source === "image" ? "Image" : "Office";
    $("#previewFlag").innerHTML = result.cached
      ? `<span class="spark">●</span> page 1 · ${src} · cache`
      : `page 1 · ${src} · ${result.ms} ms`;
  } else {
    const table = (result.table || []).length
      ? `<table class="paper-table">${result.table.map((row) => `<tr>${row.map((cell) => `<td>${esc(cell)}</td>`).join("")}</tr>`).join("")}</table>`
      : "";
    const text = result.text
      ? `<p class="paper-text">${esc(result.text)}</p>`
      : "";
    body.innerHTML = `
      <div class="paper-frame">
        <div class="paper">
          <div class="paper-title">${esc(result.title || item.name)}</div>
          <div class="paper-rule"></div>
          ${text}${table}
          ${result.note ? `<div class="paper-note">${esc(result.note)}</div>` : ""}
          <div class="paper-sheetmeta"><span>Abionic · DocSearch</span><span>Page 1</span></div>
        </div>
      </div>
      <div class="preview-meta">
        <div class="preview-name">${esc(item.name)}</div>
        <div class="preview-sub">${esc(item.folder)}</div>
        ${facts}
      </div>`;
    $("#previewFlag").textContent = result.note ? "Reconstruit" : `Texte · ${result.ms} ms`;
  }

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

async function openItem(item) {
  if (!item) return;
  const r = await Bridge.call("open_file", item.path);
  if (!r.ok) toast(r.error || "Impossible d'ouvrir le document", true);
}

/* ------------------------------------------------------------------ index */

function renderIndex() {
  if (!S.boot) return;
  const stats = S.boot.stats;
  $("#statFiles").textContent = stats.files ? fmtInt(stats.files) : "0";
  $("#statDirs").textContent = stats.dirs ? fmtInt(stats.dirs) : "0";
  $("#statSize").textContent = stats.size_human || "0 o";
  $("#statLast").textContent = stats.last_scan ? fmtDate(stats.last_scan).split(" à ")[0] : "Jamais";
  $("#statLastFoot").textContent = stats.last_scan ? fmtDate(stats.last_scan).split(" à ")[1] : "Lance une première analyse";
  $("#statFilesFoot").textContent = stats.files ? "noms et dossiers" : "index vide";
  $("#statDirsFoot").textContent = stats.dirs ? "dans l'arborescence" : "—";
  $("#statSizeFoot").textContent = "catalogués, non copiés";
  $("#scanRootsCount").textContent = fmtInt(S.cfg.roots.length);
}

function renderRoots() {
  const box = $("#roots");
  if (!S.cfg.roots.length) {
    box.innerHTML = `<div class="roots-empty">Aucun dossier pour l'instant.<br>Ajoute le lecteur réseau du service.</div>`;
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
  $("#scanRootsCount").textContent = fmtInt(S.cfg.roots.length);
}

async function persistConfig() {
  try {
    await Bridge.call("save_config", {
      roots: S.cfg.roots,
      exclude_dirs: S.cfg.exclude_dirs,
      threads: S.cfg.threads,
    });
  } catch (err) {
    toast("Réglages non enregistrés : " + err.message, true);
  }
}

async function startScan() {
  if (!S.cfg.roots.length) {
    toast("Ajoute d'abord un dossier à analyser", true);
    nav("index");
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
      $("#doneText").innerHTML = res.cancelled
        ? `${fmtInt(res.files || 0)} documents dans l'index. Le scan reprendra au prochain lancement.`
        : `${fmtInt(res.files || 0)} documents · ${fmtInt(res.dirs || 0)} dossiers · ${res.elapsed || 0} s`
          + (res.errors ? ` · ${res.errors} dossiers illisibles` : "");
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
  renderSearchMeta();
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
    select(next, true);
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
