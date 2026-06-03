/*
 * Character-feature selector dumper — paste into Chrome DevTools Console while on Flow.
 *
 * Purpose: capture language-agnostic selectors (Material-Symbol LIGATURES + role + aria +
 * accessible name) for the views gflow must UI-automate for `gflow character` (issue #145),
 * since generation is reCAPTCHA-walled and must be UI-driven (see docs/CHARACTER.md §11).
 *
 * Run it (the same snippet) in EACH of these views; each run downloads a JSON file:
 *   1. Project view, Personagens (Characters) tab selected   -> nav tab + "Novo personagem" + cards
 *   2. Inside a character editor (e.g. Denidra)               -> prompt box, generate, model, voice, personality, Concluir, slots
 *   3. Main editor: click "+" in the prompt bar to open the
 *      resource picker ("Pesquisar recursos"), Personagens    -> picker tabs + "Incluir no comando"
 *
 * Output: character-selectors.json (Chrome auto-suffixes (1)/(2) on repeat). Each file records
 * location.href + whether a dialog is open, so the view is identifiable. Send me the files.
 * Nothing sensitive is captured (no tokens/cookies) — only element metadata.
 */
(() => {
  const ICON_SEL = "i.google-symbols,.google-symbols,.material-symbols-outlined,.material-symbols-rounded,.material-icons";
  const lig = (el) => {
    const i = el.querySelector(ICON_SEL);
    return i ? i.textContent.trim() : null;
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && el.offsetParent !== null;
  };
  const desc = (el) => ({
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute("role"),
    type: el.getAttribute("type"),
    ligature: lig(el),
    ariaLabel: el.getAttribute("aria-label"),
    ariaHaspopup: el.getAttribute("aria-haspopup"),
    ariaControls: el.getAttribute("aria-controls"),
    ariaSelected: el.getAttribute("aria-selected"),
    placeholder: el.getAttribute("placeholder") || el.getAttribute("data-placeholder"),
    dataSlate: el.hasAttribute("data-slate-editor") || null,
    dataAttrs: [...el.attributes].map((a) => a.name).filter((n) => n.startsWith("data-")),
    text: (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 60) || null,
  });
  const SEL = [
    "button", "[role=tab]", "[role=menuitem]", "[role=textbox]", "[role=button]",
    "[role=option]", "a[role]", "textarea", "[contenteditable=true]", "[data-slate-editor]",
    "[aria-haspopup]",
  ].join(",");
  const els = [...document.querySelectorAll(SEL)].filter(visible);
  // de-dupe by element identity
  const seen = new Set();
  const elements = [];
  for (const el of els) {
    if (seen.has(el)) continue;
    seen.add(el);
    elements.push(desc(el));
  }
  const dialog = document.querySelector("[role=dialog]");
  const payload = {
    url: location.href,
    dialogOpen: !!dialog,
    dialogText: dialog ? (dialog.innerText || "").trim().replace(/\s+/g, " ").slice(0, 120) : null,
    capturedAt: new Date().toISOString(),
    count: elements.length,
    elements,
  };
  const blob = new Blob([JSON.stringify(payload, null, 1)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "character-selectors.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  console.log(`[dump] ${elements.length} interactive elements from ${location.href} -> ${a.download}`);
  console.table(elements.map((e) => ({ tag: e.tag, role: e.role, lig: e.ligature, name: e.ariaLabel || e.text })));
})();
