// Unit tests for static/util.js (no browser needed).
//
//   TZ=Pacific/Auckland node tests/js/test-util.mjs
//
// Needs a Node that can import an ES module from a .js file (22.7+ / 24).

import {
  renderMarkdown as md, safeCssLength, safeColor, safeBackground, labelStyle,
  parseDue, formatDue, dueState, contrastText, escapeHtml, toDatetimeLocal,
} from "../../custom_components/hakanban/static/util.js";
import { columnDropIndex, cardDropPosition } from "../../custom_components/hakanban/static/dnd.js";

let failed = 0;
let total = 0;
function eq(name, got, want) {
  total++;
  const ok = got === want;
  if (!ok) failed++;
  console.log(ok ? "PASS" : "FAIL", "-", name, ok ? "" : `\n   got:  ${JSON.stringify(got)}\n   want: ${JSON.stringify(want)}`);
}

// ---------------------------------------------------------------- markdown
const A = (href, label) => `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
eq("empty", md(""), "");
eq("bold/italic/code", md("**b** *i* `c`"), "<strong>b</strong> <em>i</em> <code>c</code>");
eq("newline -> br", md("a\nb"), "a<br>b");
eq("link", md("[x](https://a.b/c)"), A("https://a.b/c", "x"));
eq("emphasis in a link label", md("[**x**](https://a.b)"), A("https://a.b", "<strong>x</strong>"));
eq("asterisks inside a URL are kept", md("[a](https://x.y/a*b*c)"), A("https://x.y/a*b*c", "a"));
eq("backticks inside a URL are kept", md("[a](https://x.y/`z`)"), A("https://x.y/`z`", "a"));
eq("one list for consecutive bullets", md("- a\n- b"), "<ul><li>a</li><li>b</li></ul>");
eq("star bullets too", md("* a\n* b"), "<ul><li>a</li><li>b</li></ul>");
eq("text, list, text", md("t\n- a\n- b\nu"), "t<ul><li>a</li><li>b</li></ul>u");
eq("lists separated by a blank line stay separate", md("- a\n\n- b"), "<ul><li>a</li></ul><ul><li>b</li></ul>");
eq("inline markup in list items", md("- **a**"), "<ul><li><strong>a</strong></li></ul>");
eq("script is escaped", md("<script>alert(1)</script>"), "&lt;script&gt;alert(1)&lt;/script&gt;");
eq("javascript: URL is not a link", md("[x](javascript:alert(1))"), "[x](javascript:alert(1))");
eq("a quote can't break out of href", md('[x](https://a.b/"onmouseover="y)').includes('onmouseover="'), false);
eq("img onerror is escaped", md("<img src=x onerror=alert(1)>").includes("<img"), false);
eq("NULs are stripped (no forged link placeholders)", md("\u00000\u0000"), "0");
eq("forged placeholder next to a real link", md("\u00000\u0000 [a](https://q.r)").includes("undefined"), false);

// ---------------------------------------------------------- css / colours
eq("px length", safeCssLength("600px", "x"), "600px");
eq("number -> px", safeCssLength(480, "x"), "480px");
eq("vh length", safeCssLength("50vh", "x"), "50vh");
eq("markup rejected", safeCssLength("600px}</style><img src=x onerror=alert(1)>", "600px"), "600px");
eq("extra css rejected", safeCssLength("1px;background:url(x)", "600px"), "600px");
eq("empty -> fallback", safeCssLength("", "600px"), "600px");
eq("undefined -> fallback", safeCssLength(undefined, "600px"), "600px");
eq("hex colour kept", safeColor("#61bd4f"), "#61bd4f");
eq("attribute breakout rejected", safeColor('red" onmouseover="x'), "#838c91");
eq("trailing newline rejected", safeColor("#61bd4f\n"), "#838c91");
eq("css injection rejected", safeColor("#fff;background:url(x)"), "#838c91");
eq("url() background rejected", safeBackground("url(http://x)"), "");
eq("gradient background kept", safeBackground("linear-gradient(135deg,#667eea,#764ba2)"), "linear-gradient(135deg,#667eea,#764ba2)");
eq("label style of a bad value is neutral", labelStyle('"><img src=x>').includes("<"), false);
eq("short hex contrast", contrastText("#fff"), "#172b4d");
eq("escapeHtml", escapeHtml(`<a href="x">&'</a>`), "&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;");

// ------------------------------------------------------------------- dates
eq("date-only parses as the local day", parseDue("2026-10-20").getDate(), 20);
eq("date-only is local midnight", parseDue("2026-10-20").getHours(), 0);
eq("naive datetime is local wall time", parseDue("2026-10-20T09:30:00").getHours(), 9);
eq("bad date is empty", formatDue("nope"), "");
eq("bad date has no due state", dueState("nope", false), "");
eq("past due is overdue", dueState("2020-01-01", false), "overdue");
eq("formatDue shows the day", formatDue("2026-10-20").includes("20"), true);
if (process.env.TZ === "Pacific/Auckland") {
  eq("offset stamp shows NZ wall time (NZDT)", parseDue("2026-10-20T09:30:00+13:00").getHours(), 9);
  eq("date-only before the DST change is still local midnight", parseDue("2026-09-26").getHours(), 0);
}

// ------------------------------------------------- date-only due dates and local time
const day = (offset) => {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  const p2 = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
};
eq("a date-only due today is not overdue", dueState(day(0), false), "soon");
eq("a date-only due yesterday is overdue", dueState(day(-1), false), "overdue");
eq("a date-only due tomorrow is not overdue", dueState(day(1), false) !== "overdue", true);
eq("a date-only due far ahead is neither", dueState(day(30), false), "");
eq("a completed card is 'done' whatever its date", dueState(day(-5), true), "done");
eq("toDatetimeLocal: naive datetime unchanged", toDatetimeLocal("2026-10-20T09:30:00"), "2026-10-20T09:30");
eq("toDatetimeLocal: date-only has no time", toDatetimeLocal("2026-10-20"), "");
eq("toDatetimeLocal: garbage is empty", toDatetimeLocal("2026-10-20Tnope"), "");
if (process.env.TZ === "Pacific/Auckland") {
  eq("toDatetimeLocal: +13:00 stamp shows NZ wall time", toDatetimeLocal("2026-10-20T09:30:00+13:00"), "2026-10-20T09:30");
  eq("toDatetimeLocal: a UTC stamp converts to NZ time", toDatetimeLocal("2026-10-20T09:30:00Z"), "2026-10-20T22:30");
}

// ------------------------------------------------------------ drag and drop positions
// Columns [A, B, C] have midpoints 100/300/500. Dragging A removes it from the list the
// server sees, so the "others" are [B, C] with midpoints 300/500.
eq("column: drop left of everything", columnDropIndex([300, 500], 50), 0);
eq("column: drag A to between B and C", columnDropIndex([300, 500], 400), 1);
eq("column: drag A to the far right", columnDropIndex([300, 500], 900), 2);
eq("column: no other columns", columnDropIndex([], 10), 0);
// Cards in a column: [a, b, c, d]; dragging 'a' leaves [b, c, d].
eq("card: before b", cardDropPosition(["b", "c", "d"], ["b", "c", "d"], "b"), 0);
eq("card: before d", cardDropPosition(["b", "c", "d"], ["b", "c", "d"], "d"), 2);
eq("card: at the end", cardDropPosition(["b", "c", "d"], ["b", "c", "d"], null), 3);
eq("card: empty column", cardDropPosition([], [], null), 0);
// A filter hides b and c (full list [b,c,d,e], visible [d,e]); dropping before d must land
// after the hidden cards, before d.
eq("card with filter: before the first visible card", cardDropPosition(["b", "c", "d", "e"], ["d", "e"], "d"), 2);
eq("card with filter: after the last visible card", cardDropPosition(["b", "c", "d", "e"], ["d", "e"], null), 4);
eq("card with filter: hidden cards trail the visible ones",
  cardDropPosition(["b", "c", "d", "e"], ["b", "c"], null), 2);
eq("card with filter: nothing visible goes to the end", cardDropPosition(["b", "c"], [], null), 2);
eq("card: an unknown 'after' falls back to the end", cardDropPosition(["b", "c"], ["b"], "zzz"), 2);

console.log(`\n${total - failed}/${total} checks passed`);
process.exit(failed ? 1 : 0);
