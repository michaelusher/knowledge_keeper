// Small, dependency-free Markdown renderer for answers and reports.
// Everything is HTML-escaped first; only a fixed set of tags is produced, so the
// output is safe to hand to dangerouslySetInnerHTML.
// With { citations: true }, "[S1]" / "[S1, S2]" become clickable citation chips.

const esc = (s) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function inline(text, opts) {
  let s = esc(text);
  const codes = [];
  s = s.replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c);
    return `\u0000${codes.length - 1}\u0000`;
  });
  if (opts.citations) {
    s = s.replace(/\[(S\d+(?:\s*,\s*S?\d+)*)\]/g, (_, group) =>
      group
        .match(/\d+/g)
        .map((n) => `<button type="button" class="cite" data-cite="${n}" aria-label="Source ${n}">${n}</button>`)
        .join(""),
    );
  }
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/(^|[^\w])_([^_\s][^_]*?)_(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
  return s;
}

const isTableSep = (line) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(line);
const splitRow = (line) =>
  line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

export function renderMarkdown(src, opts = {}) {
  const lines = (src || "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let para = [];
  const flushPara = () => {
    if (para.length) out.push(`<p>${inline(para.join(" "), opts)}</p>`);
    para = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (/^```/.test(line.trim())) {
      flushPara();
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i].trim())) buf.push(lines[i++]);
      out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flushPara();
      const level = Math.min(h[1].length + 1, 6); // page already has an h1
      out.push(`<h${level}>${inline(h[2], opts)}</h${level}>`);
      continue;
    }
    if (line.trim().startsWith("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      flushPara();
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(splitRow(lines[i++]));
      i--;
      out.push(
        `<div class="table-wrap"><table class="table"><thead><tr>${head
          .map((c) => `<th>${inline(c, opts)}</th>`)
          .join("")}</tr></thead><tbody>${rows
          .map((r) => `<tr>${r.map((c) => `<td>${inline(c, opts)}</td>`).join("")}</tr>`)
          .join("")}</tbody></table></div>`,
      );
      continue;
    }
    const bullet = /^\s*[-*+]\s+(.*)$/;
    const numbered = /^\s*\d+[.)]\s+(.*)$/;
    if (bullet.test(line) || numbered.test(line)) {
      flushPara();
      const ordered = numbered.test(line) && !bullet.test(line);
      const re = ordered ? numbered : bullet;
      const items = [];
      while (i < lines.length && re.test(lines[i])) items.push(lines[i++].match(re)[1]);
      i--;
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>${items.map((it) => `<li>${inline(it, opts)}</li>`).join("")}</${tag}>`);
      continue;
    }
    if (/^>\s?/.test(line)) {
      flushPara();
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ""));
      i--;
      out.push(`<blockquote>${inline(buf.join(" "), opts)}</blockquote>`);
      continue;
    }
    if (/^\s*(\*\*\*|---|___)\s*$/.test(line)) {
      flushPara();
      out.push("<hr>");
      continue;
    }
    if (!line.trim()) {
      flushPara();
      continue;
    }
    para.push(line.trim());
  }
  flushPara();
  return out.join("\n");
}
