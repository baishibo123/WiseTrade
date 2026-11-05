/**
 * The seam between what moves the time cursor and what is drawn. Every driver
 * emits `PaneState`s into the same render path; drivers differ only in how they
 * fill them (which bars, which labels) and in what advances the cursor.
 *
 *   Browse  -- the user pans and zooms; paging left fetches older bars.
 *   Blind   -- a clock the user steps; the server's t1_ms bound is the cursor.
 *   Overlay -- a backtest result drawn over the bars. Seam only: no data
 *              source exists yet, and `layers` is where it will plug in.
 */

import type { ReadonlySignal } from "@preact/signals";

import type { PaneState } from "../model/pane";

/** Something drawn over a pane that is not a bar or a label. Overlay's extension point. */
export interface OverlayLayer {
  readonly paneIndex: number;
}

export interface CursorDriver {
  readonly panes: ReadonlySignal<readonly PaneState[]>;
  readonly layers: ReadonlySignal<readonly OverlayLayer[]>;
  /** The chart reports the logical range it shows; a driver may fetch in response. */
  onVisibleRange(paneIndex: number, from: number, to: number): void;
  dispose(): void;
}
