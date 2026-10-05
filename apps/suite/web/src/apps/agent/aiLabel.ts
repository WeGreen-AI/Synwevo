/** Every Agent answer is marked as AI-generated (DeepSeek Open Platform terms 8.1, and the owner's
 *  global decision), on screen and in anything copied or exported from it. */
import { chatCopy } from './chat.messages';

/** The catalogue source of the label; translate it with the page's `t`. */
export const AI_LABEL_SOURCE: string = chatCopy.aiLabel[0];

/** The answer text as it leaves the page: the label is appended once, on its own line. */
export function withAiLabel(text: string, label: string): string {
  const body = text.replace(/\s+$/, '');
  const mark = `— ${label}`;
  if (body.endsWith(mark)) return body;
  return body ? `${body}\n\n${mark}` : mark;
}
