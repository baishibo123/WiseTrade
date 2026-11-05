import type { Bar, EpochMs, Label, Period } from "../wire/types";
import type { GridSession } from "./grid";

/**
 * Everything one chart pane draws, as a cursor driver emits it. The render path
 * is the same for every driver; drivers differ only in how they fill this.
 */
export interface PaneState {
  symbol: string;
  period: Period;
  rth: boolean;
  /** Exactly what `/bars` returned, ascending. Index `i` here is the chart's logical index. */
  bars: readonly Bar[];
  /** A whole `/sessions` response covering `bars`; open-side fields only in blind mode. */
  sessions: readonly GridSession[];
  /**
   * The labels this pane may draw. Browse passes `GET /labels` filtered on symbol
   * and time only -- any other server-side filter can drop a review's target and
   * make the review look orphaned. Blind passes only this session's new labels.
   */
  labels: readonly Label[];
  cursor_ms: EpochMs | null;
}
