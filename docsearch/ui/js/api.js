/* Pont entre l'interface et Python.
   Dans l'application : window.pywebview.api (aucun réseau).
   Dans le harnais de test : petit serveur local /api/<nom>. */

const Bridge = (() => {
  let resolved = false;
  let resolveReady;
  const readyPromise = new Promise((resolve) => { resolveReady = resolve; });

  function wake(api) {
    if (resolved) return;
    resolved = true;
    resolveReady(api || null);
  }
  window.addEventListener("pywebviewready", () => {
    wake(window.pywebview && window.pywebview.api);
  });
  const timer = setInterval(() => {
    if (window.pywebview && window.pywebview.api) {
      wake(window.pywebview.api);
      clearInterval(timer);
    }
  }, 50);
  // Sans pywebview (harnais navigateur), on bascule sur HTTP après 500 ms.
  setTimeout(() => {
    if (!resolved) {
      clearInterval(timer);
      wake(null);
    }
  }, 500);

  async function call(name, ...args) {
    const api = await readyPromise;
    if (api && typeof api[name] === "function") return api[name](...args);
    const res = await fetch(`/api/${name}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ args }),
    });
    if (!res.ok) throw new Error(`API ${name} : ${res.status}`);
    return res.json();
  }

  return { ready: readyPromise, call };
})();
