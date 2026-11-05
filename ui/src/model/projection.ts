/**
 * The one projection rule (docs/interfaces.md): a bar is marked by a label when
 * the bar's own bucket `(start, t_ms]` intersects the label's coverage
 * `(cover_from_ms, cover_to_ms]`. This is how a 1d label lands on a 5m chart
 * and a 1m label on the 5m bar containing it.
 *
 * Marks are bar indexes into `PaneState.bars`, never timestamps: the renderer
 * places them by logical index and does no time arithmetic of its own.
 */

import type { Bar } from "../wire/types";
import { bucketStart } from "./grid";
import type { PaneState } from "./pane";
import { isReview, type LabelStyle } from "./reviews";

export interface LabelMark {
  labelId: number;
  /** First and last marked bar, inclusive. */
  i0: number;
  i1: number;
  /** Coverage continues past the loaded window on that side: draw no end cap. */
  clipL: boolean;
  clipR: boolean;
  style: LabelStyle;
}

/** First index in `[0, n)` for which `pred` holds; `pred` must be monotone false -> true. */
function firstWhere(n: number, pred: (i: number) => boolean): number {
  let lo = 0;
  let hi = n;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (pred(mid)) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

export function bucketStarts(p: PaneState): number[] {
  return p.bars.map((b: Bar) => bucketStart(b.t_ms, p.period, p.rth, p.sessions));
}

/**
 * Marks for every drawable label that touches a loaded bar. Reviews are not
 * drawn. On a regular-hours chart a label with `within_rth: false` is hidden
 * whole, not clipped -- it was judged with the extended session in view.
 * A label whose coverage falls between loaded bars (a data hole) gets no mark.
 */
export function projectLabels(
  p: PaneState,
  styles: ReadonlyMap<number, LabelStyle>,
  starts: readonly number[] = bucketStarts(p),
): LabelMark[] {
  const n = p.bars.length;
  const marks: LabelMark[] = [];
  if (n === 0) return marks;
  for (const l of p.labels) {
    if (isReview(l)) continue;
    if (p.rth && !l.within_rth) continue;
    const style = styles.get(l.id);
    if (style === undefined) continue;
    // Both bucket ends and bucket starts ascend, so each side is one search.
    const i0 = firstWhere(n, (i) => p.bars[i]!.t_ms > l.cover_from_ms);
    const i1 = firstWhere(n, (i) => starts[i]! >= l.cover_to_ms) - 1;
    if (i0 > i1) continue;
    marks.push({
      labelId: l.id,
      i0,
      i1,
      clipL: i0 === 0 && l.cover_from_ms < starts[0]!,
      clipR: i1 === n - 1 && l.cover_to_ms > p.bars[n - 1]!.t_ms,
      style,
    });
  }
  return marks;
}
