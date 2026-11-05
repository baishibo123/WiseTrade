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

import { CandlestickSeries, type IChartApiBase, type ISeriesApi } from "lightweight-charts";

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

  constructor(
    el: HTMLElement,
    private readonly axis: Axis<X, H>,
  ) {
    this.chart = axis.create(el);
    this.candles = this.chart.addSeries(CandlestickSeries);
  }

  setRows(rows: readonly OhlcRow<X>[]): void {
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

  dispose(): void {
    this.chart.remove();
  }
}
