// Accessibility checks that browser_audit.py runs inside each PhishAware screen.
//
// The file is one function expression. It is evaluated through the browser's
// debugging protocol, so the application's Content-Security-Policy stays in
// force and nothing is added to the page. Each check names the WCAG success
// criterion it tests. The checks complement axe-core: they cover what a rule
// engine cannot decide from the markup alone (computed contrast against the
// real background, the size of touch targets, and clipped or overflowing text).
(options) => {
  const results = {checked: {}, failures: [], advisories: []};
  const FOCUSABLE = "a[href], button, input:not([type=hidden]), select, textarea, summary, " +
    "[tabindex]:not([tabindex='-1'])";
  const count = (check, n = 1) => { results.checked[check] = (results.checked[check] || 0) + n; };
  const fail = (check, criterion, element, detail) =>
    results.failures.push({check, criterion, element: describe(element), detail});
  const advise = (check, criterion, element, detail) =>
    results.advisories.push({check, criterion, element: describe(element), detail});

  function describe(element) {
    if (!element || !element.tagName) return "page";
    const name = element.tagName.toLowerCase();
    const cls = typeof element.className === "string" && element.className.trim()
      ? "." + element.className.trim().split(/\s+/).join(".") : "";
    const text = (element.getAttribute("aria-label") || element.textContent || "")
      .trim().replace(/\s+/g, " ").slice(0, 40);
    return `${name}${cls}${text ? ` "${text}"` : ""}`;
  }

  // ---- Colour arithmetic (WCAG 2.1 definitions of relative luminance and contrast) ----
  function parseColor(value) {
    const match = /rgba?\(([^)]+)\)/.exec(value || "");
    if (!match) return null;
    const parts = match[1].split(/[,\s/]+/).filter(Boolean).map(parseFloat);
    return {r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1};
  }
  function over(top, bottom) {   // composite a translucent colour over an opaque one
    return {
      r: top.r * top.a + bottom.r * (1 - top.a),
      g: top.g * top.a + bottom.g * (1 - top.a),
      b: top.b * top.a + bottom.b * (1 - top.a),
      a: 1,
    };
  }
  function backgroundOf(element) {   // the colour actually painted behind an element
    const layers = [];
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.backgroundImage !== "none") return null;   // cannot be judged automatically
      const color = parseColor(style.backgroundColor);
      if (color && color.a > 0) {
        layers.push(color);
        if (color.a === 1) break;
      }
    }
    let color = {r: 255, g: 255, b: 255, a: 1};   // the default canvas
    for (let i = layers.length - 1; i >= 0; i -= 1) color = over(layers[i], color);
    return color;
  }
  function luminance(color) {
    const channel = (value) => {
      const v = value / 255;
      // 0.04045 is the sRGB threshold in the current text of WCAG 2.1. Earlier
      // texts printed 0.03928; no 8-bit colour falls between the two values.
      return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b);
  }
  function contrast(a, b) {
    const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (light + 0.05) / (dark + 0.05);
  }
  const hex = (c) => "#" + [c.r, c.g, c.b].map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");

  function isVisible(element) {
    const style = getComputedStyle(element);
    if (style.display === "none" || style.visibility !== "visible" || +style.opacity === 0) return false;
    const box = element.getBoundingClientRect();
    return box.width > 1 && box.height > 1;   // 1 px boxes are the visually-hidden utility
  }

  // A stable key for a tab stop. A group of radio buttons is one stop.
  const everything = Array.from(document.querySelectorAll("*"));
  const stopKey = (element) => element.matches("input[type=radio]")
    ? `radio:${element.name}` : `element:${everything.indexOf(element)}`;

  // ---- Mode "expected-stops": the controls a keyboard user must be able to reach ----
  if (options.mode === "expected-stops") {
    return Array.from(document.querySelectorAll(FOCUSABLE))
      .filter((element) => !element.disabled && isVisible(element))
      .map((element) => ({key: stopKey(element), element: describe(element)}));
  }

  // ---- Mode "focus": 2.4.7 Focus Visible and 1.4.11 for the focused control ----
  if (options.mode === "focus") {
    const element = document.activeElement;
    if (!element || element === document.body || element === document.documentElement) return null;
    const style = getComputedStyle(element);
    const ring = parseColor(style.outlineColor);
    const around = backgroundOf(element.parentElement);
    const drawn = style.outlineStyle !== "none" && parseFloat(style.outlineWidth) >= 2;
    return {
      key: stopKey(element),
      element: describe(element),
      visible: drawn,
      ring: ring ? hex(ring) : null,
      around: around ? hex(around) : null,
      contrast: drawn && ring && around ? +contrast(over(ring, around), around).toFixed(2) : null,
    };
  }

  // ---- 1.4.3 Contrast (Minimum): every visible text node against its background ----
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const reported = new Set();
  while (walker.nextNode()) {
    const element = walker.currentNode.parentElement;
    if (!walker.currentNode.nodeValue.trim() || !element || !isVisible(element)) continue;
    if (["SCRIPT", "STYLE", "TITLE"].includes(element.tagName)) continue;
    const style = getComputedStyle(element);
    const inSvg = element instanceof SVGElement;
    const foreground = parseColor(inSvg ? style.fill : style.color);
    const background = backgroundOf(inSvg ? element.closest("svg").parentElement : element);
    count("text-contrast");
    if (!foreground || !background) {
      advise("text-contrast", "1.4.3", element, "background is an image; check by eye");
      continue;
    }
    const size = parseFloat(style.fontSize);
    const bold = parseInt(style.fontWeight, 10) >= 700;
    const required = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
    const ratio = contrast(over(foreground, background), background);
    const key = `${hex(foreground)}/${hex(background)}/${required}`;
    if (ratio < required && !reported.has(key)) {
      reported.add(key);
      fail("text-contrast", "1.4.3", element,
        `${hex(foreground)} on ${hex(background)} is ${ratio.toFixed(2)}:1; needs ${required}:1`);
    }
  }

  // ---- 1.4.11 Non-text Contrast: the boundary of text fields; chart bars (advisory) ----
  document.querySelectorAll("input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select, textarea")
    .forEach((field) => {
      if (!isVisible(field)) return;
      const style = getComputedStyle(field);
      const around = backgroundOf(field.parentElement);
      const border = parseColor(style.borderTopColor);
      const fill = backgroundOf(field);
      count("field-boundary");
      const best = Math.max(
        border && parseFloat(style.borderTopWidth) > 0 ? contrast(over(border, around), around) : 1,
        fill ? contrast(fill, around) : 1);
      if (best < 3) {
        fail("field-boundary", "1.4.11", field,
          `boundary ${border ? hex(border) : "none"} on ${hex(around)} is ${best.toFixed(2)}:1; needs 3:1`);
      }
    });
  document.querySelectorAll("svg rect.bar").forEach((bar) => {
    const style = getComputedStyle(bar);
    const fill = parseColor(style.fill);
    const stroke = parseFloat(style.strokeWidth) > 0 ? parseColor(style.stroke) : null;
    const around = backgroundOf(bar.closest("svg").parentElement);
    count("chart-bar");
    if (!fill || !around) return;
    // A bar can be told from the page by its fill or by its outline.
    const best = Math.max(contrast(fill, around), stroke ? contrast(over(stroke, around), around) : 1);
    if (best < 3 && !reported.has("bar" + hex(fill))) {
      reported.add("bar" + hex(fill));
      advise("chart-bar", "1.4.11", bar,
        `bar ${hex(fill)} on ${hex(around)} is ${best.toFixed(2)}:1; ` +
        "each bar also carries its value as text");
    }
  });

  // ---- 4.1.2 Name, Role, Value and 1.1.1 Non-text Content: accessible names ----
  function accessibleName(element) {
    const ids = (element.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    const referenced = ids.map((id) => (document.getElementById(id) || {}).textContent || "").join(" ").trim();
    if (referenced) return referenced;
    const label = (element.getAttribute("aria-label") || "").trim();
    if (label) return label;
    if (element.labels && element.labels.length) {
      const text = Array.from(element.labels).map((l) => l.textContent).join(" ").trim();
      if (text) return text;
    }
    if (element.tagName === "IMG") return (element.getAttribute("alt") || "").trim();
    return (element.textContent || element.getAttribute("title") || element.value || "").trim();
  }
  const interactive = Array.from(document.querySelectorAll(FOCUSABLE));
  interactive.forEach((element) => {
    count("accessible-name");
    if (!accessibleName(element)) fail("accessible-name", "4.1.2", element, "control has no accessible name");
  });
  document.querySelectorAll("img").forEach((image) => {
    count("text-alternative");
    if (!image.hasAttribute("alt")) fail("text-alternative", "1.1.1", image, "image has no alt attribute");
  });
  document.querySelectorAll("svg").forEach((svg) => {
    count("text-alternative");
    const hidden = svg.closest("[aria-hidden='true']") !== null;
    const named = svg.getAttribute("role") === "img" && accessibleName(svg);
    if (!hidden && !named) fail("text-alternative", "1.1.1", svg, "graphic is neither named nor hidden");
  });
  document.querySelectorAll("input[type=radio]").forEach((radio) => {
    count("group-label");
    const group = radio.closest("fieldset");
    if (!group || !group.querySelector("legend") || !group.querySelector("legend").textContent.trim()) {
      fail("group-label", "1.3.1", radio, "radio button is not in a fieldset with a legend");
    }
  });

  // ---- Page structure: 3.1.1, 2.4.2, 1.3.1, 2.4.1, 2.4.3 ----
  count("structure", 6);
  if (!(document.documentElement.getAttribute("lang") || "").trim()) {
    fail("structure", "3.1.1", null, "the page does not declare its language");
  }
  if (!document.title.trim()) fail("structure", "2.4.2", null, "the page has no title");
  if (document.querySelectorAll("h1").length !== 1) {
    fail("structure", "1.3.1", null, `the page has ${document.querySelectorAll("h1").length} h1 headings`);
  }
  let previous = 0;
  document.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach((heading) => {
    const level = +heading.tagName[1];
    if (previous && level > previous + 1) {
      fail("structure", "1.3.1", heading, `heading level jumps from h${previous} to h${level}`);
    }
    previous = level;
  });
  if (document.querySelectorAll("main").length !== 1) {
    fail("structure", "1.3.1", null, "the page needs exactly one main landmark");
  }
  const skip = document.querySelector("body a[href^='#']");
  if (!skip || skip !== interactive[0] || !document.querySelector(skip.getAttribute("href"))) {
    fail("structure", "2.4.1", skip, "the first control is not a working skip link");
  }
  // A repeated id breaks a page only when a label, an ARIA attribute, or a link
  // points at it: the reference then resolves to the wrong element (1.3.1).
  // Criterion 4.1.1 Parsing, which used to cover every repeated id, is always
  // satisfied in the current text of WCAG 2.1 and was removed from WCAG 2.2.
  const ids = Array.from(document.querySelectorAll("[id]")).map((e) => e.id);
  const referenced = new Set();
  document.querySelectorAll("[for], [aria-labelledby], [aria-describedby], [aria-controls], a[href^='#']")
    .forEach((element) => ["for", "aria-labelledby", "aria-describedby", "aria-controls"]
      .map((name) => element.getAttribute(name) || "")
      .concat((element.getAttribute("href") || "").replace(/^#/, ""))
      .join(" ").split(/\s+/).filter(Boolean).forEach((id) => referenced.add(id)));
  Array.from(new Set(ids.filter((id, i) => ids.indexOf(id) !== i))).forEach((id) => {
    const report = referenced.has(id) ? fail : advise;
    report("structure", "1.3.1", document.getElementById(id), `id "${id}" is used more than once`);
  });
  if (document.querySelector("[tabindex]:not([tabindex='0']):not([tabindex='-1'])")) {
    fail("structure", "2.4.3", null, "a positive tabindex changes the natural focus order");
  }

  // ---- 1.4.10 Reflow: no horizontal scrolling, and nothing pushed off the side ----
  if (options.reflow) {
    count("reflow");
    const width = document.documentElement.clientWidth;
    if (document.documentElement.scrollWidth > width + 1) {
      fail("reflow", "1.4.10", null,
        `the page is ${document.documentElement.scrollWidth} px wide in a ${width} px window`);
    }
    document.querySelectorAll("body *").forEach((element) => {
      if (!isVisible(element) || element.closest(".table-scroll, svg, .visually-hidden, .skip-link")) return;
      const box = element.getBoundingClientRect();
      const parent = element.parentElement.getBoundingClientRect();
      const outside = (b) => b.right > width + 1 || b.left < -1;
      if (outside(box) && !outside(parent)) {   // report the outermost element only
        fail("reflow", "1.4.10", element, `extends to ${Math.round(box.right)} px in a ${width} px window`);
      }
    });
  }

  // ---- 2.5.8 Target Size (Minimum), WCAG 2.2 ----
  // A target passes when it is at least 24 by 24 CSS pixels. A smaller target
  // passes through the criterion's spacing exception: a circle 24 px across,
  // centred on the target, must not touch another target or the circle of
  // another undersized target.
  if (options.targets) {
    const targets = [];
    interactive.forEach((element) => {
      // A checkbox or radio button is operated through its whole label.
      const target = element.matches("input[type=checkbox], input[type=radio]")
        ? (element.closest("label") || element) : element;
      if (!isVisible(target) || targets.some((known) => known.element === target)) return;
      // Links inside a sentence are exempt from the criterion.
      const inline = target.tagName === "A" && target.parentElement.tagName === "P" &&
        target.parentElement.textContent.trim() !== target.textContent.trim();
      if (inline) return;
      const box = target.getBoundingClientRect();
      targets.push({
        element: target, box, small: Math.min(box.width, box.height) < 24,
        x: box.left + box.width / 2, y: box.top + box.height / 2,
      });
    });
    const RADIUS = 12;
    const touchesBox = (circle, box) => {   // does the circle overlap the rectangle?
      const dx = circle.x - Math.max(box.left, Math.min(circle.x, box.right));
      const dy = circle.y - Math.max(box.top, Math.min(circle.y, box.bottom));
      return dx * dx + dy * dy < RADIUS * RADIUS;
    };
    const touchesCircle = (a, b) => Math.hypot(a.x - b.x, a.y - b.y) < 2 * RADIUS;
    targets.forEach((target) => {
      count("target-size");
      if (!target.small) return;
      const crowded = targets.find((other) => other !== target &&
        (other.small ? touchesCircle(target, other) : touchesBox(target, other.box)));
      const size = `${Math.round(target.box.width)} by ${Math.round(target.box.height)} px`;
      if (crowded) {
        fail("target-size", "2.5.8", target.element,
          `target is ${size} and its 24 px circle touches ${describe(crowded.element)}`);
      } else {
        advise("target-size", "2.5.8", target.element,
          `target is ${size}; it passes because nothing is within its 24 px circle`);
      }
    });
  }

  // ---- 1.4.12 Text Spacing: the user's wider spacing must not clip any text ----
  if (options.spacing) {
    // The spacing a reader may set, from the success criterion. A style sheet built
    // through the CSS object model is not an inline style, so the CSP allows it.
    const sheet = new CSSStyleSheet();
    sheet.replaceSync("* { line-height: 1.5 !important; letter-spacing: 0.12em !important; " +
      "word-spacing: 0.16em !important; } p { margin-bottom: 2em !important; }");
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
    count("text-spacing");
    document.querySelectorAll("body *").forEach((element) => {
      if (!isVisible(element) || !element.textContent.trim()) return;
      const overflow = getComputedStyle(element).overflow;
      const clips = overflow.includes("hidden") || overflow.includes("clip");
      if (clips && (element.scrollHeight > element.clientHeight + 1 || element.scrollWidth > element.clientWidth + 1)) {
        fail("text-spacing", "1.4.12", element, "text is clipped when the reader widens the spacing");
      }
    });
    if (document.documentElement.scrollWidth > document.documentElement.clientWidth + 1) {
      fail("text-spacing", "1.4.12", null, "wider spacing forces horizontal scrolling");
    }
    document.adoptedStyleSheets = document.adoptedStyleSheets.filter((s) => s !== sheet);
  }

  return results;
}
