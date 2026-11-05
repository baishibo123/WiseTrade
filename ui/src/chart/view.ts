/**
 * One chart pane. The only module that draws with lightweight-charts, and the
 * part of the component that is axis-agnostic: x reaches the chart only through
 * `Axis<X, H>`, so an index axis plugs in without changing this file.
 *
 * Invariant: the series holds exactly one data point per row, in row order --
 * no whitespace points, no merging -- so the chart's logical index `i` is
 * `rows[i]`. A click resolves to a label stamp through that identity; an extra
 * point anywhere would shift every later bar by one, invisibly.
 */

import {
  CandlestickSeries,
  type IChartApiBase,
  type ISeriesApi,
  type LogicalRange,
} from "lightweight-charts";

import type { Axis } from "./axis";

export interface OhlcRow<X> {
  x: X;
  open: number;
  high: number;
  low: number;
  close: number;
}

export class ChartView<X, H> {
  private readonly chart: IChartApiBase<H>;
  private readonly candles: ISeriesApi<"Candlestick", H>;
  /** True while `setRows` moves the view itself; such moves are not the user's. */
  private applying = false;

  constructor(
    el: HTMLElement,
    private readonly axis: Axis<X, H>,
  ) {
    this.chart = axis.create(el);
    this.candles = this.chart.addSeries(CandlestickSeries);
  }

  /**
   * Replace the rows. `prepended` > 0 says that many rows were added on the left
   * of what was shown, and the view shifts by as many logical indexes so the
   * bars under the user's eye stay put while history pages in. `reset` scrolls
   * to the newest bar, for a new symbol, period or session filter.
   */
  setRows(rows: readonly OhlcRow<X>[], prepended = 0, reset = false): void {
    const ts = this.chart.timeScale();
    const before = prepended > 0 ? ts.getVisibleLogicalRange() : null;
    this.applying = true;
    try {
      this.applyRows(rows);
      if (before !== null) {
        ts.setVisibleLogicalRange({ from: before.from + prepended, to: before.to + prepended });
      } else if (reset) {
        // Not scrollToRealTime: it animates, and a page landing mid-animation
        // would be shifted relative to a view that is still moving.
        ts.scrollToPosition(0, false);
      }
    } finally {
      this.applying = false;
    }
  }

  private applyRows(rows: readonly OhlcRow<X>[]): void {
    this.candles.setData(
      rows.map((r) => ({
        time: this.axis.toHorz(r.x),
        open: r.open,
        high: r.high,
        low: r.low,
        close: r.close,
      })),
    );
  }

  /** Report the visible logical range as it changes; returns the unsubscribe. */
  onVisibleRange(cb: (from: number, to: number) => void): () => void {
    const ts = this.chart.timeScale();
    const h = (r: LogicalRange | null) => {
      if (r !== null && !this.applying) cb(r.from, r.to);
    };
    ts.subscribeVisibleLogicalRangeChange(h);
    return () => ts.unsubscribeVisibleLogicalRangeChange(h);
  }

  dispose(): void {
    this.chart.remove();
  }
}
