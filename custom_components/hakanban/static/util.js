// Small dependency-free helpers shared across the panel and card.

export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Tiny markdown subset: `code`, **bold**, *italic*, [links](https://…) and "- " / "* " lists.
// Everything is HTML-escaped first, so the only markup in the output is what this builds.
const inlineMarkdown = (text) =>
  text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");

export function renderMarkdown(text) {
  if (!text) return "";
  // Links are lifted out before emphasis is applied so a "*" or "`" inside a URL stays
  // part of the URL; they are put back at the end. NUL can't appear in the input.
  const links = [];
  const escaped = escapeHtml(String(text).replace(/\0/g, "")).replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, label, url) => {
      links.push(`<a href="${url}" target="_blank" rel="noopener noreferrer">${inlineMarkdown(label)}</a>`);
      return `\0${links.length - 1}\0`;
    }
  );

  const out = [];
  let list = null; // consecutive bullet lines share one <ul>
  for (const line of escaped.split("\n")) {
    const bullet = /^[-*] (.+)$/.exec(line);
    if (bullet) {
      if (!list) out.push((list = []));
      list.push(`<li>${inlineMarkdown(bullet[1])}</li>`);
    } else {
      list = null;
      out.push(inlineMarkdown(line));
    }
  }
  const html = out
    .map((part) => (Array.isArray(part) ? `<ul>${part.join("")}</ul>` : part))
    .join("<br>")
    // a list is a block, so no <br> next to it
    .replace(/<br>(<ul>)/g, "$1")
    .replace(/(<\/ul>)<br>/g, "$1");
  return html.replace(/\0(\d+)\0/g, (_, i) => links[Number(i)]);
}

// A CSS length from untrusted config (e.g. a dashboard card's ``height``); anything that
// isn't a plain number with a unit falls back, so it can't carry extra CSS or markup.
export function safeCssLength(value, fallback) {
  const v = typeof value === "number" ? `${value}px` : String(value ?? "").trim();
  return /^\d+(?:\.\d+)?(?:px|em|rem|vh|vw|%)$/.test(v) ? v : fallback;
}

// Colours and backgrounds are written into inline ``style`` attributes, so only values of
// the exact shapes the backend accepts are allowed through; anything else (including data
// stored before the backend validated it) falls back to a neutral value.
const HEX_COLOR = /^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;
const GRADIENT = /^linear-gradient\(\d{1,3}deg,\s*#[0-9a-fA-F]{3,8},\s*#[0-9a-fA-F]{3,8}\)$/;

export function safeColor(value, fallback = "#838c91") {
  return typeof value === "string" && HEX_COLOR.test(value) ? value : fallback;
}

export function safeBackground(value) {
  return typeof value === "string" && (HEX_COLOR.test(value) || GRADIENT.test(value)) ? value : "";
}

// Inline style for a label chip: a safe background plus a readable text colour.
export function labelStyle(color) {
  const c = safeColor(color);
  return `background:${c};color:${contrastText(c)}`;
}

// Parse a stored due value. A date-only value ("2026-10-20") means that day in the
// viewer's timezone; ``new Date("2026-10-20")`` would be UTC midnight, which shows as
// the previous day anywhere west of UTC.
export function parseDue(iso) {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  const d = m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDue(iso) {
  if (!iso) return "";
  const d = parseDue(iso);
  if (!d) return "";
  const hasTime = iso.includes("T");
  const opts = hasTime
    ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
    : { month: "short", day: "numeric" };
  return d.toLocaleString(undefined, opts);
}

export function dueState(iso, complete) {
  if (!iso || complete) return complete ? "done" : "";
  const now = new Date();
  const d = parseDue(iso);
  if (!d) return "";
  const soon = new Date(now.getTime() + 24 * 3600 * 1000);
  if (d < now) return "overdue";
  if (d < soon) return "soon";
  return "";
}

// Pick a readable text colour for a label background.
export function contrastText(hex) {
  if (!hex || hex[0] !== "#") return "#fff";
  // Expand #rgb / #rgba so every accepted form is read the same way.
  const full = hex.length <= 5 ? "#" + [...hex.slice(1)].map((c) => c + c).join("") : hex;
  const r = parseInt(full.slice(1, 3), 16);
  const g = parseInt(full.slice(3, 5), 16);
  const b = parseInt(full.slice(5, 7), 16);
  return r * 0.299 + g * 0.587 + b * 0.114 > 150 ? "#172b4d" : "#fff";
}

export function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}
