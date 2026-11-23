/* Pont entre l'interface et Python.
   Dans l'application : window.pywebview.api (aucun réseau).
   Dans le harnais de test : petit serveur local /api/<nom>. */

const Bridge = (() => {
  let resolved = false;
  let resolveReady;
  const readyPromise = new Promise((resolve) => { resolveReady = resolve; });

  // Le harnais de test sert l'interface en HTTP sur localhost. L'application,
  // elle, charge la page depuis l'hôte virtuel de pywebview : un repli HTTP y
  // répondait « 405 » et masquait la vraie erreur.
  const devServer = /^https?:$/.test(location.protocol)
    && /^(localhost|127\.0\.0\.1)$/.test(location.hostname);

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
      clearInterval(timer);
      wake(window.pywebview.api);
    }
  }, 50);

  if (devServer) {
    // Harnais navigateur : on bascule sur HTTP après 500 ms.
    setTimeout(() => {
      if (!resolved) {
        clearInterval(timer);
        wake(null);
      }
    }, 500);
  } else {
    // Patience : le premier lancement d'un exe onefile est lent (extraction,
    // antivirus). On attend le pont, sans jamais appeler le réseau.
    setTimeout(() => {
      if (resolved) return;
      clearInterval(timer);
      resolved = true;
      resolveReady(Promise.reject(new Error(
        "Le moteur de l'application n'a pas répondu. Fermez la fenêtre et relancez Abionic DocSearch.")));
    }, 30000);
  }

  async function call(name, ...args) {
    const api = await readyPromise;
    if (api && typeof api[name] === "function") return api[name](...args);
    if (!devServer) throw new Error("Le moteur de l'application n'est pas disponible.");
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
