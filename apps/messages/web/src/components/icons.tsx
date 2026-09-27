/** Inline icons drawn in `currentColor`, so they take the text colour of the control they sit
 *  in and need no colour of their own (the design guard scans for literal colours). Decorative
 *  by default (`aria-hidden`): the control they belong to carries the label. */
import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { size?: number };
const base = (size: number, p: P) => ({
  width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const, "aria-hidden": true, focusable: false, ...p,
});

export const IcBack = ({ size = 18, ...p }: P) => <svg {...base(size, p)}><path d="M15 5l-7 7 7 7" /></svg>;
export const IcInfo = ({ size = 18, ...p }: P) => <svg {...base(size, p)}><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></svg>;
export const IcMore = ({ size = 18, ...p }: P) => <svg {...base(size, p)} fill="currentColor" stroke="none"><circle cx="5" cy="12" r="1.8" /><circle cx="12" cy="12" r="1.8" /><circle cx="19" cy="12" r="1.8" /></svg>;
export const IcReply = ({ size = 18, ...p }: P) => <svg {...base(size, p)}><path d="M9 14L4 9l5-5" /><path d="M4 9h9a7 7 0 017 7v3" /></svg>;
export const IcEmoji = ({ size = 18, ...p }: P) => <svg {...base(size, p)}><circle cx="12" cy="12" r="9" /><path d="M8.5 14.5a4.5 4.5 0 007 0" /><path d="M9 10h.01M15 10h.01" strokeWidth="2.4" /></svg>;
export const IcAttach = ({ size = 18, ...p }: P) => <svg {...base(size, p)}><path d="M21 12.5l-8.2 8.2a5.5 5.5 0 01-7.8-7.8l9-9a3.5 3.5 0 015 5l-9 9a1.5 1.5 0 01-2.1-2.1L16 7.7" /></svg>;
export const IcSend = ({ size = 18, ...p }: P) => <svg {...base(size, p)}><path d="M4 12l16-8-6 16-2.5-6.5L4 12z" /><path d="M11.5 13.5L20 4" /></svg>;
export const IcClose = ({ size = 18, ...p }: P) => <svg {...base(size, p)}><path d="M6 6l12 12M18 6L6 18" /></svg>;
export const IcSearch = ({ size = 18, ...p }: P) => <svg {...base(size, p)}><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></svg>;
export const IcPin = ({ size = 14, ...p }: P) => <svg {...base(size, p)}><path d="M14 4l6 6-3 1-4 4 .5 4.5L9 15l-5 5 5-5-4.5-4.5L9 10l4-4 1-2z" /></svg>;
export const IcMute = ({ size = 14, ...p }: P) => <svg {...base(size, p)}><path d="M6 8v8h3l5 4V4L9 8H6z" /><path d="M18 9l3 3-3 3M21 9l-3 3 3 3" strokeWidth="1.6" /></svg>;
export const IcGroup = ({ size = 18, ...p }: P) => <svg {...base(size, p)}><circle cx="9" cy="8" r="3.2" /><circle cx="17" cy="9" r="2.6" /><path d="M3 19a6 6 0 0112 0" /><path d="M15 19a4.5 4.5 0 016-4" /></svg>;
export const IcImage = ({ size = 18, ...p }: P) => <svg {...base(size, p)}><rect x="3.5" y="4.5" width="17" height="15" rx="2" /><circle cx="9" cy="10" r="1.6" /><path d="M20 16l-5-5-8 8" /></svg>;
export const IcFile = ({ size = 18, ...p }: P) => <svg {...base(size, p)}><path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5z" /><path d="M14 3v5h5" /></svg>;
export const IcDownload = ({ size = 18, ...p }: P) => <svg {...base(size, p)}><path d="M12 4v11M7 10l5 5 5-5" /><path d="M5 20h14" /></svg>;
export const IcCheck = ({ size = 14, ...p }: P) => <svg {...base(size, p)}><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>;
export const IcPlus = ({ size = 18, ...p }: P) => <svg {...base(size, p)}><path d="M12 5v14M5 12h14" /></svg>;
export const IcEdit = ({ size = 16, ...p }: P) => <svg {...base(size, p)}><path d="M4 20h4l11-11-4-4L4 16v4z" /><path d="M13 7l4 4" /></svg>;
export const IcTrash = ({ size = 16, ...p }: P) => <svg {...base(size, p)}><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></svg>;
export const IcFlag = ({ size = 16, ...p }: P) => <svg {...base(size, p)}><path d="M5 21V4M5 4h11l-1.5 4L16 12H5" /></svg>;
export const IcCopy = ({ size = 16, ...p }: P) => <svg {...base(size, p)}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 012-2h9" /></svg>;
export const IcArrowDown = ({ size = 16, ...p }: P) => <svg {...base(size, p)}><path d="M12 4v15M6 13l6 6 6-6" /></svg>;
export const IcArrowUp = ({ size = 16, ...p }: P) => <svg {...base(size, p)}><path d="M12 20V5M6 11l6-6 6 6" /></svg>;
export const IcAt = ({ size = 18, ...p }: P) => <svg {...base(size, p)}><circle cx="12" cy="12" r="3.5" /><path d="M15.5 12v1.5a2.5 2.5 0 005 0V12a8.5 8.5 0 10-3.3 6.7" /></svg>;
export const IcSettings = ({ size = 16, ...p }: P) => <svg {...base(size, p)}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z" /></svg>;
export const IcClock = ({ size = 12, ...p }: P) => <svg {...base(size, p)}><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></svg>;
export const IcWarn = ({ size = 14, ...p }: P) => <svg {...base(size, p)}><path d="M12 3l10 18H2L12 3z" /><path d="M12 10v4M12 17.5h.01" strokeWidth="2.2" /></svg>;
