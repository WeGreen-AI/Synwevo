/** A small, safe Markdown renderer for the Agent's answers: headings, paragraphs, emphasis, inline
 *  and fenced code, lists, tables and http(s) links. It builds React elements — never HTML strings —
 *  so nothing in an answer (or in business data quoted by it) can inject markup or script. */
import { Fragment, type ReactNode } from "react";

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  // Order matters: code first (its content is literal), then links, bold, italic.
  const re = /(`[^`\n]+`)|(\[[^\]\n]{1,300}\]\((https?:\/\/[^\s)]{1,2000})\))|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${key}-${i++}`;
    if (m[1]) out.push(<code key={k}>{m[1].slice(1, -1)}</code>);
    else if (m[2]) {
      const label = m[2].slice(1, m[2].indexOf("]("));
      out.push(<a key={k} href={m[3]} target="_blank" rel="noopener noreferrer nofollow">{label}</a>);
    } else if (m[4]) out.push(<strong key={k}>{m[4].slice(2, -2)}</strong>);
    else if (m[5]) out.push(<em key={k}>{m[5].slice(1, -1)}</em>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function withBreaks(text: string, key: string): ReactNode[] {
  const lines = text.split("\n");
  return lines.flatMap((line, i) => i === 0 ? inline(line, `${key}-l${i}`) : [<br key={`${key}-br${i}`} />, ...inline(line, `${key}-l${i}`)]);
}

function isTableSep(line: string) {
  return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
}

function cells(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let n = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${n++}`;
    if (/^```/.test(line)) {
      const lang = line.slice(3).trim();
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) body.push(lines[i++]);
      i++;
      blocks.push(<pre key={key} className="ag-code" data-lang={lang || undefined}><code>{body.join("\n")}</code></pre>);
      continue;
    }
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length;
      const content = inline(h[2], key);
      blocks.push(level === 1 ? <h3 key={key}>{content}</h3> : level === 2 ? <h4 key={key}>{content}</h4> : <h5 key={key}>{content}</h5>);
      i++;
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(cells(lines[i++]));
      blocks.push(<div key={key} className="ag-table-wrap"><table className="ag-table">
        <thead><tr>{head.map((c, j) => <th key={j}>{inline(c, `${key}-h${j}`)}</th>)}</tr></thead>
        <tbody>{rows.map((r, ri) => <tr key={ri}>{head.map((_, j) => <td key={j}>{inline(r[j] ?? "", `${key}-${ri}-${j}`)}</td>)}</tr>)}</tbody>
      </table></div>);
      continue;
    }
    if (/^\s*([-*]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+[.)])\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*]|\d+[.)])\s+/, ""));
        i++;
      }
      const lis = items.map((it, j) => <li key={j}>{inline(it, `${key}-${j}`)}</li>);
      blocks.push(ordered ? <ol key={key}>{lis}</ol> : <ul key={key}>{lis}</ul>);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^```/.test(lines[i]) && !/^(#{1,3})\s+/.test(lines[i])
           && !/^\s*([-*]|\d+[.)])\s+/.test(lines[i]) && !(lines[i].includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1]))) {
      para.push(lines[i++]);
    }
    blocks.push(<p key={key}>{withBreaks(para.join("\n"), key)}</p>);
  }
  return <Fragment>{blocks}</Fragment>;
}
