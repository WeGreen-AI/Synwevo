import { useId } from 'react';
import { type Bar, moneyText } from './walletModel';

/** Charged amount per day (columns) or per model / conversation / member (rows), for one unit.
 *
 *  Drawn with the shared tokens only. The figure is an image with a one-sentence summary for
 *  screen readers; the table next to it on the page carries every value, so nothing here is the
 *  only way to read a number. Each bar also has a native tooltip (<title>). */
export default function UsageChart({ bars, unit, scale, locale, daily, summary, empty }: {
  bars: Bar[]; unit: string; scale: number; locale: string; daily: boolean; summary: string; empty: string;
}) {
  const id = useId();
  if (!bars.length || bars.every(bar => bar.value === '0')) return <p className="usage-chart-empty muted">{empty}</p>;
  if (!daily) {
    return <figure className="usage-chart" aria-labelledby={id}>
      <figcaption id={id} className="sr-only">{summary}</figcaption>
      <ol className="usage-rows" aria-hidden="true">{bars.map(bar => <li key={bar.key}>
        <span className="usage-row-label" title={bar.label}>{bar.label}</span>
        <span className="usage-row-track"><span className="usage-row-bar" style={{ inlineSize: `${Math.max(bar.ratio * 100, bar.value === '0' ? 0 : 1)}%` }} /></span>
        <span className="usage-row-value">{moneyText(bar.value, scale, unit, locale)}</span>
      </li>)}</ol>
    </figure>;
  }
  // Bars stretch with the page (no fixed aspect); the date labels are HTML so they keep their
  // size on a phone instead of shrinking with the drawing.
  const width = 1000, height = 160, gap = bars.length > 60 ? 0.6 : 2;
  const slot = width / bars.length, bar = Math.max(slot - gap, 0.6);
  const every = Math.ceil(bars.length / 6);
  return <figure className="usage-chart" aria-labelledby={id}>
    <figcaption id={id} className="sr-only">{summary}</figcaption>
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-labelledby={id} className="usage-columns">
      <line x1="0" x2={width} y1={height - 0.5} y2={height - 0.5} className="usage-axis" />
      {bars.map((item, index) => {
        const h = item.ratio * (height - 4);
        return <rect key={item.key} x={index * slot + gap / 2} y={height - h} width={bar}
          height={Math.max(h, item.value === '0' ? 0 : 1)} className="usage-column">
          <title>{`${item.key} · ${moneyText(item.value, scale, unit, locale)} · ${item.calls}`}</title>
        </rect>;
      })}
    </svg>
    <div className="usage-ticks" aria-hidden="true">{bars.map((item, index) => index % every === 0
      ? <span key={item.key} style={{ insetInlineStart: `${((index + 0.5) / bars.length) * 100}%` }}>{item.label}</span> : null)}</div>
  </figure>;
}
