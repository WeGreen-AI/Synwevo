/** "Export conversation": the branch on screen as a Markdown file the person saves.
 *
 *  Only the text people saw leaves the page: questions and answers, never reasoning, tool inputs,
 *  costs or content hidden after an access change. Every answer is followed by the AI label
 *  (DeepSeek Open Platform terms 8.1; owner decision: global), exactly as "Copy answer" does. */
import { withAiLabel } from './aiLabel';
import type { Message } from './types';

export interface ExportWords {
  /** Heading for the person's own messages. */
  you: string;
  /** Heading for answers. */
  agent: string;
  /** Stands in for a message hidden after an access change. */
  hidden: string;
  /** The AI-content label, in the reader's language. */
  label: string;
}

const oneLine = (text: string) => text.replace(/\s+/g, ' ').trim();

export function conversationMarkdown(title: string, messages: Pick<Message, 'role' | 'body' | 'hidden'>[], words: ExportWords): string {
  const parts = [`# ${oneLine(title) || words.agent}`];
  for (const m of messages) {
    if (m.role !== 'user' && m.role !== 'assistant') continue;
    const who = m.role === 'user' ? words.you : words.agent;
    if (m.hidden) { parts.push(`## ${who}`, words.hidden); continue; }
    const body = (m.body ?? '').replace(/\s+$/, '').replace(/^\n+/, '');
    if (!body.trim()) continue;
    parts.push(`## ${who}`, m.role === 'assistant' ? withAiLabel(body, words.label) : body);
  }
  return `${parts.join('\n\n')}\n`;
}

/** A file name every system accepts: the title without path or reserved characters, then the
 *  UTC date. */
export function exportFileName(title: string, date: Date): string {
  const name = Array.from(oneLine(title.replace(/[\\/:*?"<>|\u0000-\u001f\u007f]+/g, ' ')).replace(/^\.+/, ''))
    .slice(0, 80).join('').trim() || 'conversation';
  return `${name}-${date.toISOString().slice(0, 10)}.md`;
}

/** Hand the person a text file to save (no server round trip, nothing stored). */
export function downloadText(text: string, fileName: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
