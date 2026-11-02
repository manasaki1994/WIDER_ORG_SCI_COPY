// Methods page (methods-explainer D8): why WIDER works this way. #methods lists the topics, #methods/<topic> opens one,
// #methods/<topic>/<short|why|auditors> opens it at that layer, #methods/glossary the glossary and
// #methods/glossary/<slug> one term. The text is the same as `wider guide methods [<topic>]` prints. It is built from
// DOM nodes with textContent: paragraphs, "- " items and `code` spans; nothing in it is parsed as HTML. Needs no project.
(function () {
  const q = (s) => document.querySelector(s);
  const GLOSSARY = "glossary";
  // GET /api/methods: the guide panel's titles ask for it once; the Methods page asks anew each time it opens, since
  // the files are read as they are (guide-assistant D22)
  let index = null;
  const getIndex = (fresh) => ((fresh && (index = null)), index ||= api("/api/methods").catch((e) => { index = null; throw e; }));

  // the line under each title, as `wider guide methods` prints it (wider/guide/methods:review_line)
  const reviewLine = (r) => (r && r.status === "reviewed" && r.date) ? `Reviewed by the author on ${r.date}.`
    : (r && r.edited && r.date) ? `Edited since the author's review on ${r.date}; not yet reviewed again.`
    : "Drafted; not yet reviewed by the author.";

  function node(tag, text, attrs) {
    const e = document.createElement(tag);
    if (text != null) e.textContent = text;
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    return e;
  }
  // `code` spans; every other character stays text
  function inline(parent, text) {
    String(text).split("`").forEach((part, i) => {
      if (part) parent.appendChild(i % 2 ? node("code", part) : document.createTextNode(part));
    });
    return parent;
  }
  // paragraphs (blank-line separated), "- " items (an indented line continues the item)
  function blocks(text) {
    const out = []; let p = null, ul = null;
    for (const line of String(text || "").split("\n")) {
      const t = line.trim();
      if (!t) { p = ul = null; continue; }
      if (t.startsWith("- ")) {
        if (!ul) { ul = node("ul"); out.push(ul); }
        p = null; ul.appendChild(inline(node("li"), t.slice(2)));
      } else if (ul && /^\s/.test(line)) {
        const li = ul.lastElementChild; li.appendChild(document.createTextNode(" ")); inline(li, t);
      } else {
        ul = null;
        if (p) p.appendChild(document.createTextNode(" ")); else { p = node("p"); out.push(p); }
        inline(p, t);
      }
    }
    return out;
  }

  function drawList(d, open) {
    const nav = q("#methods-list"); nav.textContent = "";
    // a file that cannot be read is left out and named above the list (D22)
    for (const p of d.problems || []) nav.appendChild(node("p", p.text, { class: "methods-problem" }));
    const ul = node("ul");
    const item = (href, title, context, review, on) => {
      const li = node("li"), a = node("a", title, { href });
      if (on) a.setAttribute("aria-current", "page");
      li.appendChild(a);
      if (context) li.appendChild(node("p", context, { class: "methods-context" }));
      li.appendChild(node("p", reviewLine(review), { class: "methods-review" }));
      ul.appendChild(li);
    };
    for (const t of d.topics) item(`#methods/${t.id}`, t.title, t.context, t.review, t.id === open);
    item(`#methods/${GLOSSARY}`, "Glossary", "The words WIDER uses, with the other names they go by.", d.glossary_review,
         open === GLOSSARY);
    nav.appendChild(ul);
  }

  // the element whose id is `want` gets the view's top and the focus (each layer heading and term has its anchor as id)
  function scrollTo(anchors, want) {
    const e = want && anchors[want]; if (!e) return;
    e.tabIndex = -1; e.scrollIntoView({ block: "start" }); e.focus({ preventScroll: true });
  }

  function drawTopic(view, t, layer) {
    const anchors = {};
    view.appendChild(node("h2", t.title, { id: `methods/${t.id}` }));
    view.appendChild(node("p", t.context, { class: "methods-context" }));
    view.appendChild(node("p", reviewLine(t.review), { class: "methods-review" }));
    for (const l of t.layers) {
      const id = `methods/${t.id}/${l.layer}`;
      const h = anchors[id] = node("h3", l.heading, { id });
      view.appendChild(h);
      blocks(l.text).forEach((b) => view.appendChild(b));
    }
    // on a phone the list sits above the topic, so an opened topic is brought to its title
    const narrow = !layer && window.matchMedia && window.matchMedia("(max-width: 640px)").matches;
    if (narrow) anchors[`methods/${t.id}`] = view.firstElementChild;
    scrollTo(anchors, layer ? `methods/${t.id}/${layer}` : narrow && `methods/${t.id}`);
  }

  function drawGlossary(view, d, slug) {
    const anchors = {};
    view.appendChild(node("h2", "Glossary", { id: `methods/${GLOSSARY}` }));
    view.appendChild(node("p", reviewLine(d.glossary_review), { class: "methods-review" }));
    const dl = node("dl", null, { class: "methods-glossary" });
    for (const e of d.glossary) {
      const id = `methods/${GLOSSARY}/${e.slug}`;
      const dt = anchors[id] = node("dt", e.term, { id });
      dl.appendChild(dt);
      const dd = node("dd");
      if (e.synonyms && e.synonyms.length) dd.appendChild(node("p", `Also called: ${e.synonyms.join("; ")}.`));
      blocks(e.definition).forEach((b) => dd.appendChild(b));
      dl.appendChild(dd);
    }
    view.appendChild(dl);
    scrollTo(anchors, slug && `methods/${GLOSSARY}/${slug}`);
  }

  // `sub` is the part of the address after "#methods/": "", "<topic>", "<topic>/<layer>", "glossary" or "glossary/<slug>"
  async function load(sub) {
    const [open, deeper] = String(sub || "").split("/");
    const d = await getIndex(true);
    const known = !open || open === GLOSSARY || d.topics.some((t) => t.id === open);
    const status = q("#methods-status");
    const bad = (d.problems || []).find((p) => p.file === `${open}.md`);
    status.textContent = known ? "" : bad ? bad.text : `there is no such methods topic: ${open}`;   // the CLI's sentence
    status.hidden = known;
    drawList(d, known ? open : null);
    const view = q("#methods-view"); view.textContent = "";
    if (!open || !known) return;
    if (open === GLOSSARY) return drawGlossary(view, d, deeper);
    drawTopic(view, await api(`/api/methods/${encodeURIComponent(open)}`), deeper);
  }

  // titles by id, for the guide panel's "Why WIDER works this way:" links (shell.js)
  const titles = () => getIndex().then((d) => Object.fromEntries(d.topics.map((t) => [t.id, t.title])));

  window.Methods = { load, titles, reviewLine };
})();
