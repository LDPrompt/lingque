/**
 * LingQue Browser Helper — Chrome Extension Content Script
 *
 * Runs in the page MAIN world so Playwright can call it via page.evaluate().
 * Provides deep DOM analysis that surpasses external JS injection:
 *   - Shadow DOM traversal
 *   - Multi-strategy selector generation
 *   - Structural similarity detection (like RPA element cloning)
 *   - Page structure analysis
 */
(function () {
  "use strict";
  if (window.__lingque) return;

  // ───────────────────── helpers ─────────────────────

  const MAX_TEXT = 80;
  const _trim = (s, n) => (s || "").trim().replace(/\s+/g, " ").slice(0, n || MAX_TEXT);

  function _isVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const st = getComputedStyle(el);
    return st.display !== "none" && st.visibility !== "hidden" && st.opacity !== "0";
  }

  function _isInteractive(el) {
    const tag = el.tagName.toLowerCase();
    if (["a", "button", "input", "select", "textarea"].includes(tag)) return true;
    const role = el.getAttribute("role") || "";
    if (["button", "link", "tab", "menuitem", "checkbox", "radio",
         "switch", "option", "combobox", "textbox", "searchbox"].includes(role)) return true;
    if (el.getAttribute("contenteditable") === "true") return true;
    if (el.hasAttribute("onclick") || el.hasAttribute("data-click") ||
        el.hasAttribute("data-spm") || el.hasAttribute("data-e2e")) return true;
    if (el.hasAttribute("tabindex") && el.getAttribute("tabindex") !== "-1") return true;
    try { if (getComputedStyle(el).cursor === "pointer") return true; } catch (_) {}
    return false;
  }

  function _bbox(el) {
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
  }

  // ───────────────────── selector engine ─────────────────────

  function _cssEscape(s) {
    try { return CSS.escape(s); } catch (_) { return s.replace(/([^\w-])/g, "\\$1"); }
  }

  function _buildCssPath(el) {
    const parts = [];
    let cur = el;
    while (cur && cur !== document.documentElement) {
      if (cur.nodeType !== 1) break;
      let seg = cur.tagName.toLowerCase();
      if (cur.id) {
        parts.unshift("#" + _cssEscape(cur.id));
        break;
      }
      const parent = cur.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
        if (siblings.length > 1) {
          const idx = siblings.indexOf(cur) + 1;
          seg += `:nth-of-type(${idx})`;
        }
      }
      parts.unshift(seg);
      cur = parent;
    }
    return parts.join(" > ");
  }

  function _buildXPath(el) {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1) {
      let tag = cur.tagName.toLowerCase();
      if (cur.id) {
        parts.unshift(`//${tag}[@id="${cur.id}"]`);
        return parts.join("/");
      }
      const parent = cur.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
        if (siblings.length > 1) {
          const idx = siblings.indexOf(cur) + 1;
          tag += `[${idx}]`;
        }
      }
      parts.unshift(tag);
      cur = parent;
    }
    return "/" + parts.join("/");
  }

  function _dataAttrSelector(el) {
    for (const attr of ["data-testid", "data-e2e", "data-sku", "data-spm",
                         "data-click", "data-id", "data-item-id"]) {
      const v = el.getAttribute(attr);
      if (v) return `[${attr}="${v}"]`;
    }
    return "";
  }

  function _textSelector(el) {
    const text = _trim(el.innerText || el.textContent, 40);
    if (!text || text.length < 2) return "";
    return text;
  }

  function _ariaSelector(el) {
    const label = el.getAttribute("aria-label");
    if (label) return `[aria-label="${label}"]`;
    const role = el.getAttribute("role");
    const name = _trim(el.innerText || el.textContent, 30);
    if (role && name) return `role:${role}:${name}`;
    return "";
  }

  function _uniqueSelector(el) {
    if (el.id) return "#" + _cssEscape(el.id);
    const da = _dataAttrSelector(el);
    if (da) return da;
    const tag = el.tagName.toLowerCase();
    const name = el.getAttribute("name");
    if (name && ["input", "select", "textarea"].includes(tag)) {
      return `${tag}[name="${name}"]`;
    }
    const ph = el.getAttribute("placeholder");
    if (ph) return `${tag}[placeholder="${ph}"]`;
    return _buildCssPath(el);
  }

  function _getSelectors(el) {
    return {
      css: _buildCssPath(el),
      xpath: _buildXPath(el),
      uniquePath: _uniqueSelector(el),
      dataAttr: _dataAttrSelector(el),
      text: _textSelector(el),
      aria: _ariaSelector(el),
    };
  }

  function _selectorStability(sels) {
    let score = 0;
    if (sels.dataAttr) score += 3;
    if (sels.uniquePath && sels.uniquePath.startsWith("#")) score += 3;
    if (sels.aria && sels.aria.startsWith("[aria-label")) score += 2;
    if (sels.text) score += 1;
    if (sels.css && sels.css.split(">").length <= 3) score += 1;
    return Math.min(score, 10);
  }

  // ───────────────────── role detection ─────────────────────

  function _detectRole(el) {
    const tag = el.tagName.toLowerCase();
    const type = (el.type || "").toLowerCase();
    const role = el.getAttribute("role") || "";
    if (tag === "a") return "link";
    if (tag === "button" || role === "button" || (tag === "input" && (type === "submit" || type === "button"))) return "button";
    if (tag === "summary") return "button";
    if (tag === "input" && type === "checkbox") return "checkbox";
    if (tag === "input" && type === "radio") return "radio";
    if (tag === "input" || tag === "textarea" || el.getAttribute("contenteditable") === "true") return "textbox";
    if (tag === "select" || role === "combobox" || role === "listbox") return "combobox";
    if (role === "switch") return "switch";
    if (role === "tab") return "tab";
    if (role === "menuitem") return "menuitem";
    if (role === "option") return "option";
    if (role === "link") return "link";
    if (role) return role;
    if (_isInteractive(el)) return "button";
    return "";
  }

  function _detectName(el) {
    const tag = el.tagName.toLowerCase();
    const type = (el.type || "").toLowerCase();
    const ariaLabel = el.getAttribute("aria-label") || "";
    const placeholder = el.placeholder || "";
    const text = _trim(el.innerText || el.textContent);
    const value = el.value || "";
    const alt = el.alt || el.title || "";

    if (tag === "a") return text || ariaLabel || _trim(el.getAttribute("href"), 40) || alt;
    if (tag === "button" || el.getAttribute("role") === "button") return text || ariaLabel || value || alt;
    if (tag === "input" && (type === "submit" || type === "button")) return value || text || ariaLabel;
    if (tag === "input" || tag === "textarea") {
      let label = placeholder || ariaLabel || el.name || "";
      if (!label && type === "tel") label = "手机号";
      if (!label && type === "password") label = "密码";
      if (!label && type === "search") label = "搜索";
      if (!label && type === "email") label = "邮箱";
      if (!label) label = type || "文本输入";
      return label;
    }
    if (tag === "select") return ariaLabel || el.name || text;
    if (el.getAttribute("contenteditable") === "true") return ariaLabel || _trim(text, 30) || "editor";
    if (tag === "img") return alt || ariaLabel || "";
    return text || ariaLabel || alt;
  }

  // ───────────────────── context & state detection ─────────────────────

  function _detectContext(el) {
    let cur = el.parentElement;
    let depth = 0;
    const parts = [];
    while (cur && depth < 6) {
      const tag = cur.tagName.toLowerCase();
      if (tag === "form") {
        const formName = cur.getAttribute("name") || cur.getAttribute("aria-label")
          || cur.id || cur.getAttribute("data-testid") || "";
        parts.unshift(formName ? formName + "表单" : "表单");
        break;
      }
      if (tag === "dialog" || cur.getAttribute("role") === "dialog") {
        const title = cur.querySelector("h1,h2,h3,[class*='title'],[class*='header']");
        parts.unshift(title ? _trim(title.innerText, 20) + "弹窗" : "弹窗");
        break;
      }
      if (tag === "nav" || cur.getAttribute("role") === "navigation") {
        const label = cur.getAttribute("aria-label") || "";
        parts.unshift(label ? label + "导航" : "导航栏");
        break;
      }
      if (tag === "section" || tag === "fieldset" || cur.getAttribute("role") === "group") {
        const heading = cur.querySelector("h1,h2,h3,h4,legend,[class*='title'],[class*='header']");
        if (heading) {
          parts.unshift(_trim(heading.innerText, 20));
          break;
        }
      }
      if (tag === "header" || tag === "footer" || tag === "aside" || tag === "main") {
        parts.unshift(tag);
        break;
      }
      cur = cur.parentElement;
      depth++;
    }
    return parts.join(" > ") || "";
  }

  function _detectNeighbors(el) {
    const parent = el.parentElement;
    if (!parent) return "";
    const siblings = Array.from(parent.children).filter(function(c) {
      return _isVisible(c) && c !== el;
    });
    const idx = Array.from(parent.children).indexOf(el);
    const hints = [];
    for (let i = idx - 1; i >= 0 && hints.length < 1; i--) {
      const sib = parent.children[i];
      if (!sib || !_isVisible(sib)) continue;
      const tag = sib.tagName.toLowerCase();
      if (tag === "label" || _isInteractive(sib)) {
        hints.push("前:" + _trim(sib.innerText || sib.textContent, 15));
      }
    }
    for (let i = idx + 1; i < parent.children.length && hints.length < 2; i++) {
      const sib = parent.children[i];
      if (!sib || !_isVisible(sib)) continue;
      const tag = sib.tagName.toLowerCase();
      if (tag === "label" || _isInteractive(sib)) {
        hints.push("后:" + _trim(sib.innerText || sib.textContent, 15));
      }
    }
    return hints.join(", ");
  }

  function _detectState(el) {
    const states = [];
    if (el.disabled || el.getAttribute("aria-disabled") === "true") {
      states.push("disabled");
    }
    if (el.required || el.getAttribute("aria-required") === "true") {
      states.push("required");
    }
    if (el.checked || el.getAttribute("aria-checked") === "true") {
      states.push("checked");
    }
    if (el.getAttribute("aria-expanded") === "true") {
      states.push("expanded");
    } else if (el.getAttribute("aria-expanded") === "false") {
      states.push("collapsed");
    }
    if (el.getAttribute("aria-selected") === "true") {
      states.push("selected");
    }
    if (el.readOnly || el.getAttribute("aria-readonly") === "true") {
      states.push("readonly");
    }
    var tag = el.tagName.toLowerCase();
    if ((tag === "input" || tag === "textarea") && !el.value) {
      states.push("empty");
    } else if ((tag === "input" || tag === "textarea") && el.value) {
      states.push("filled");
    }
    return states.join(",");
  }

  function _detectAriaDescription(el) {
    var describedBy = el.getAttribute("aria-describedby");
    if (describedBy) {
      var descEl = document.getElementById(describedBy);
      if (descEl) return _trim(descEl.innerText || descEl.textContent, 50);
    }
    return el.getAttribute("aria-description") || "";
  }

  // ───────────────────── dialog detection ─────────────────────

  const _DIALOG_SELECTORS = [
    "dialog[open]", '[role="dialog"]', '[role="alertdialog"]',
    ".modal.show", ".modal.active", '.modal[style*="display: block"]',
    '.ant-modal-wrap:not([style*="display: none"])',
    '.el-dialog__wrapper:not([style*="display: none"])',
    '[class*="login-dialog"]', '[class*="loginDialog"]',
    '[class*="login-modal"]', '[class*="loginModal"]',
    '[class*="SignFlow"]', '[class*="sign-flow"]',
    ".next-overlay-wrapper .next-dialog",
    ".fm-login", ".login-box", ".login-panel", ".login-container",
  ];

  function _findDialog() {
    for (const sel of _DIALOG_SELECTORS) {
      try {
        const d = document.querySelector(sel);
        if (d && _isVisible(d)) return d;
      } catch (_) {}
    }
    return null;
  }

  // ───────────────────── deep DOM scanner ─────────────────────

  function _walkTree(root, visitor, depth) {
    if (depth > 15) return;
    if (!root) return;
    const children = root.children || [];
    for (let i = 0; i < children.length; i++) {
      const child = children[i];
      if (visitor(child) === false) return;
      if (child.shadowRoot) {
        _walkTree(child.shadowRoot, visitor, depth + 1);
      }
      _walkTree(child, visitor, depth + 1);
    }
  }

  function scanElements(opts) {
    opts = opts || {};
    const maxElements = opts.maxElements || 150;
    const viewportOnly = opts.viewportOnly !== false;
    const results = [];
    const seen = new Set();
    const dialogRoot = _findDialog();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    function process(el, inDialog, inShadow) {
      if (results.length >= maxElements) return false;
      if (!_isVisible(el)) return;

      const role = _detectRole(el);
      if (!role) return;
      const name = _detectName(el);
      if (!name || name.length < 1) return;

      const rect = el.getBoundingClientRect();
      if (viewportOnly && (rect.bottom < -50 || rect.top > vh + 50 ||
                           rect.right < -50 || rect.left > vw + 50)) return;

      const key = role + "|" + name;
      if (seen.has(key)) return;
      seen.add(key);

      const sels = _getSelectors(el);
      results.push({
        role: role,
        name: name.slice(0, MAX_TEXT),
        tag: el.tagName.toLowerCase(),
        bbox: _bbox(el),
        selectors: sels,
        stability: _selectorStability(sels),
        visible: true,
        interactive: _isInteractive(el),
        in_dialog: inDialog,
        in_shadow: inShadow,
        disabled: el.disabled || el.getAttribute("aria-disabled") === "true" || false,
        checked: el.checked || false,
        value: _trim(el.value, 30),
        required: el.required || false,
        context: _detectContext(el),
        neighbors: _detectNeighbors(el),
        state: _detectState(el),
        ariaDescription: _detectAriaDescription(el),
      });
    }

    if (dialogRoot) {
      _walkTree(dialogRoot, (el) => process(el, true, false), 0);
      dialogRoot.querySelectorAll("*").forEach((el) => {
        if (el.shadowRoot) _walkTree(el.shadowRoot, (c) => process(c, true, true), 0);
      });
    }

    _walkTree(document.body, (el) => {
      if (dialogRoot && dialogRoot.contains(el)) return;
      const inShadow = el.getRootNode() !== document;
      process(el, false, inShadow);
    }, 0);

    if (results.length < 30) {
      const candidates = document.querySelectorAll("div, span, li, img, td");
      for (const el of candidates) {
        if (results.length >= maxElements) break;
        try {
          const st = getComputedStyle(el);
          if (st.cursor !== "pointer") continue;
          if (!_isVisible(el)) continue;
          if (el.closest("a, button, input, select, textarea")) continue;
          const text = _trim(el.innerText || el.textContent, 60);
          const alt = el.alt || el.title || el.getAttribute("aria-label") || "";
          const lbl = text || alt;
          if (!lbl || lbl.length < 2) continue;
          const key = "button|" + lbl;
          if (seen.has(key)) continue;
          seen.add(key);
          var fbSels = _getSelectors(el);
          results.push({
            role: "button", name: lbl.slice(0, MAX_TEXT), tag: el.tagName.toLowerCase(),
            bbox: _bbox(el), selectors: fbSels,
            stability: _selectorStability(fbSels),
            visible: true, interactive: true,
            in_dialog: false, in_shadow: false,
            disabled: false, checked: false, value: "", required: false,
            context: _detectContext(el),
            neighbors: _detectNeighbors(el),
            state: "",
            ariaDescription: "",
          });
        } catch (_) {}
      }
    }

    return results;
  }

  // ───────────────────── structural signature & findSimilar ─────────────────────

  function _structSignature(el) {
    const tag = el.tagName.toLowerCase();
    const childTags = Array.from(el.children).map(c => c.tagName.toLowerCase()).join(",");
    const cls = (el.className || "").toString().replace(/[\d]+/g, "*").split(/\s+/).sort().join(" ");
    return `${tag}|${childTags}|${cls}`;
  }

  function _findListContainer(el) {
    let cur = el.parentElement;
    let depth = 0;
    while (cur && depth < 8) {
      const sig = _structSignature(el);
      let matchCount = 0;
      for (const child of cur.children) {
        if (_structSignature(child) === sig) matchCount++;
      }
      if (matchCount >= 2) {
        return { container: cur, signature: sig, referenceElement: el };
      }
      el = cur;
      cur = cur.parentElement;
      depth++;
    }
    return null;
  }

  function _extractFields(el, fields) {
    if (!fields || !Object.keys(fields).length) {
      const data = {};
      const text = _trim(el.innerText || el.textContent, 200);
      if (text) data.text = text;
      const img = el.querySelector("img");
      if (img) data.image = img.src || img.getAttribute("data-src") || "";
      const link = el.querySelector("a[href]");
      if (link) data.link = link.href || "";
      const price = el.querySelector('[class*="price"], [class*="Price"]');
      if (price) data.price = _trim(price.innerText || price.textContent, 30);
      return data;
    }

    const data = {};
    for (const [key, sel] of Object.entries(fields)) {
      try {
        const target = el.querySelector(sel);
        if (!target) { data[key] = ""; continue; }
        if (target.tagName === "IMG") {
          data[key] = target.src || target.getAttribute("data-src") || "";
        } else if (target.tagName === "A") {
          data[key] = { text: _trim(target.innerText, 60), href: target.href || "" };
        } else {
          data[key] = _trim(target.innerText || target.textContent, 120);
        }
      } catch (_) {
        data[key] = "";
      }
    }
    return data;
  }

  function findSimilar(cssSelector, opts) {
    opts = opts || {};
    const fields = opts.fields || {};
    const maxItems = opts.maxItems || 100;

    let refEl;
    try { refEl = document.querySelector(cssSelector); } catch (_) { return { error: "无效选择器: " + cssSelector }; }
    if (!refEl) return { error: "未找到元素: " + cssSelector };

    const info = _findListContainer(refEl);
    if (!info) {
      return {
        error: "未找到列表容器，该元素可能不在重复列表中",
        suggestion: "尝试用 analyzePage() 先识别页面中的列表区域",
      };
    }

    const { container, signature } = info;
    const items = [];
    for (const child of container.children) {
      if (items.length >= maxItems) break;
      if (_structSignature(child) !== signature) continue;
      if (!_isVisible(child)) continue;
      items.push({
        index: items.length,
        selector: _uniqueSelector(child),
        bbox: _bbox(child),
        data: _extractFields(child, fields),
      });
    }

    return {
      container: _uniqueSelector(container),
      itemCount: items.length,
      signature: signature,
      items: items,
    };
  }

  // ───────────────────── getSelectors (public) ─────────────────────

  function getSelectors(cssSelector) {
    let el;
    try { el = document.querySelector(cssSelector); } catch (_) { return { error: "无效选择器" }; }
    if (!el) return { error: "未找到元素" };
    const sels = _getSelectors(el);
    sels.stabilityScore = _selectorStability(sels);
    return sels;
  }

  // ───────────────────── page analyzer ─────────────────────

  function analyzePage() {
    const result = { lists: [], forms: [], tables: [], nav: [], regions: [] };

    document.querySelectorAll("ul, ol, [role='list'], [role='listbox'], [role='menu']").forEach((el) => {
      if (!_isVisible(el)) return;
      const items = el.querySelectorAll(":scope > li, :scope > [role='listitem'], :scope > [role='option'], :scope > [role='menuitem']");
      if (items.length >= 2) {
        result.lists.push({
          selector: _uniqueSelector(el),
          tag: el.tagName.toLowerCase(),
          itemCount: items.length,
          sampleTexts: Array.from(items).slice(0, 3).map(i => _trim(i.innerText, 40)),
        });
      }
    });

    const containers = document.querySelectorAll("div, section, main");
    for (const container of containers) {
      if (!_isVisible(container)) continue;
      const children = Array.from(container.children);
      if (children.length < 3) continue;

      const sigs = {};
      for (const child of children) {
        const sig = _structSignature(child);
        sigs[sig] = (sigs[sig] || 0) + 1;
      }

      for (const [sig, count] of Object.entries(sigs)) {
        if (count >= 3) {
          const matching = children.filter(c => _structSignature(c) === sig && _isVisible(c));
          if (matching.length >= 3) {
            const alreadyFound = result.lists.some(l => {
              try { return container.querySelector(l.selector) !== null; } catch (_) { return false; }
            });
            if (!alreadyFound) {
              result.lists.push({
                selector: _uniqueSelector(container),
                tag: container.tagName.toLowerCase(),
                itemCount: matching.length,
                itemSignature: sig,
                sampleTexts: matching.slice(0, 3).map(m => _trim(m.innerText, 40)),
              });
            }
          }
        }
      }
      if (result.lists.length > 20) break;
    }

    document.querySelectorAll("form").forEach((el) => {
      if (!_isVisible(el)) return;
      const inputs = el.querySelectorAll("input, select, textarea");
      result.forms.push({
        selector: _uniqueSelector(el),
        action: el.action || "",
        method: el.method || "GET",
        inputCount: inputs.length,
        fieldNames: Array.from(inputs).slice(0, 8).map(i => i.name || i.placeholder || i.type || "").filter(Boolean),
      });
    });

    document.querySelectorAll("table").forEach((el) => {
      if (!_isVisible(el)) return;
      const rows = el.querySelectorAll("tr");
      const headers = Array.from(el.querySelectorAll("th")).map(h => _trim(h.innerText, 30));
      result.tables.push({
        selector: _uniqueSelector(el),
        rowCount: rows.length,
        headers: headers,
      });
    });

    document.querySelectorAll("nav, [role='navigation']").forEach((el) => {
      if (!_isVisible(el)) return;
      const links = el.querySelectorAll("a");
      result.nav.push({
        selector: _uniqueSelector(el),
        linkCount: links.length,
        sampleLinks: Array.from(links).slice(0, 5).map(a => ({
          text: _trim(a.innerText, 30),
          href: a.href || "",
        })),
      });
    });

    return result;
  }

  // ───────────────────── extractList ─────────────────────

  function extractList(containerSelector, itemSelector, fieldMap) {
    let container;
    try { container = document.querySelector(containerSelector); } catch (_) {
      return { error: "无效容器选择器: " + containerSelector };
    }
    if (!container) return { error: "未找到容器: " + containerSelector };

    const items = container.querySelectorAll(itemSelector || ":scope > *");
    const rows = [];
    for (const item of items) {
      if (!_isVisible(item)) continue;
      rows.push({
        index: rows.length,
        selector: _uniqueSelector(item),
        data: _extractFields(item, fieldMap || {}),
      });
    }
    return { container: containerSelector, itemCount: rows.length, rows: rows };
  }

  // ───────────────────── elementAt ─────────────────────

  function elementAt(x, y) {
    const el = document.elementFromPoint(x, y);
    if (!el) return { error: "坐标处无元素" };
    const interactive = _isInteractive(el) ? el : el.closest("a, button, [role='button'], input, select, textarea, [onclick]");
    const target = interactive || el;
    return {
      tag: target.tagName.toLowerCase(),
      role: _detectRole(target),
      name: _detectName(target),
      selectors: _getSelectors(target),
      bbox: _bbox(target),
      interactive: _isInteractive(target),
      isOriginal: target === el,
    };
  }

  // ───────────────────── DOM change watcher ─────────────────────

  let _domChanged = false;
  let _observer = null;

  function watchDom() {
    if (_observer) return;
    _domChanged = false;
    let _pending = null;

    _observer = new MutationObserver(function (mutations) {
      if (_pending) return;
      _pending = setTimeout(function () {
        _pending = null;
        let addedInteractive = 0;
        let removedInteractive = 0;
        for (const m of mutations) {
          for (const n of m.addedNodes) {
            if (n.nodeType === 1 && _isInteractive(n)) addedInteractive++;
          }
          for (const n of m.removedNodes) {
            if (n.nodeType === 1 && _isInteractive(n)) removedInteractive++;
          }
        }
        if (addedInteractive + removedInteractive >= 3) {
          _domChanged = true;
        }
      }, 200);
    });

    _observer.observe(document.body || document.documentElement, {
      childList: true,
      subtree: true,
    });
  }

  function resetDomChanged() {
    var was = _domChanged;
    _domChanged = false;
    return was;
  }

  function isDomChanged() {
    return _domChanged;
  }

  // ───────────────────── pagination detection ─────────────────────

  function findPagination() {
    const candidates = [];

    const textPatterns = ["下一页", "下页", "Next", "next", "»", "›", ">"];
    const allClickable = document.querySelectorAll("a, button, [role='button'], [onclick], li");

    for (const el of allClickable) {
      if (!_isVisible(el)) continue;
      const text = _trim(el.innerText || el.textContent, 20);
      const ariaLabel = el.getAttribute("aria-label") || "";
      const title = el.title || "";
      const combined = text + " " + ariaLabel + " " + title;

      for (const pat of textPatterns) {
        if (combined.includes(pat)) {
          const disabled = el.classList.contains("disabled") ||
                           el.hasAttribute("disabled") ||
                           el.getAttribute("aria-disabled") === "true" ||
                           el.classList.contains("pn-disabled");
          candidates.push({
            selector: _uniqueSelector(el),
            text: text,
            disabled: disabled,
            priority: 1,
            bbox: _bbox(el),
          });
          break;
        }
      }
    }

    const cssCandidates = document.querySelectorAll(
      '[class*="next"]:not([class*="prev"]), .pn-next, .pagination .next, ' +
      '.page-next, [class*="page-next"], [class*="pageNext"]'
    );
    for (const el of cssCandidates) {
      if (!_isVisible(el)) continue;
      const sel = _uniqueSelector(el);
      if (candidates.some(function(c) { return c.selector === sel; })) continue;
      const disabled = el.classList.contains("disabled") ||
                       el.hasAttribute("disabled") ||
                       el.getAttribute("aria-disabled") === "true";
      candidates.push({
        selector: sel,
        text: _trim(el.innerText || el.textContent, 20),
        disabled: disabled,
        priority: 2,
        bbox: _bbox(el),
      });
    }

    const paginationContainers = document.querySelectorAll(
      '.pagination, [class*="pager"], [class*="Pagination"], nav[aria-label*="page"], ' +
      '[class*="page-list"], [class*="pageList"]'
    );
    for (const container of paginationContainers) {
      if (!_isVisible(container)) continue;
      const active = container.querySelector(
        '.active, .current, [class*="active"], [aria-current="page"]'
      );
      if (!active) continue;
      let next = active.nextElementSibling;
      if (!next && active.parentElement) {
        const parent = active.parentElement;
        const nextParent = parent.nextElementSibling;
        if (nextParent) next = nextParent.querySelector("a, button") || nextParent;
      }
      if (next && _isVisible(next)) {
        const sel = _uniqueSelector(next);
        if (!candidates.some(function(c) { return c.selector === sel; })) {
          candidates.push({
            selector: sel,
            text: _trim(next.innerText || next.textContent, 20),
            disabled: false,
            priority: 3,
            bbox: _bbox(next),
          });
        }
      }
    }

    candidates.sort(function(a, b) { return a.priority - b.priority; });

    var enabled = candidates.filter(function(c) { return !c.disabled; });
    return {
      found: enabled.length > 0,
      bestMatch: enabled[0] || null,
      allCandidates: candidates.slice(0, 5),
      isLastPage: candidates.length > 0 && enabled.length === 0,
    };
  }

  // ───────────────────── expose API ─────────────────────

  window.__lingque = {
    version: "1.2.0",
    scanElements: scanElements,
    findSimilar: findSimilar,
    getSelectors: getSelectors,
    analyzePage: analyzePage,
    extractList: extractList,
    elementAt: elementAt,
    watchDom: watchDom,
    isDomChanged: isDomChanged,
    resetDomChanged: resetDomChanged,
    findPagination: findPagination,
    _domChanged: false,
  };

  watchDom();
})();
