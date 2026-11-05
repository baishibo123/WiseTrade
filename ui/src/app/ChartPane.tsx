/**
 * Mounts one imperative `ChartView` and feeds it from a driver's pane signal.
 * The chart lives outside the component tree (ADR-029): this component renders
 * once, and pane updates reach the chart through a signal effect, not a render.
 */

import { effect } from "@preact/signals";
import { useEffect, useRef } from "preact/hooks";

import { timeAxis } from "../chart/axis";
import { ChartView } from "../chart/view";
import type { CursorDriver } from "../drivers/driver";
import type { Bar } from "../wire/types";

/** How many bars `next` added on the left of `prev`, or 0 if it is not a pure prepend. */
export function prependedCount(prev: readonly Bar[], next: readonly Bar[]): number {
  const k = next.length - prev.length;
  if (prev.length === 0 || k <= 0) return 0;
  return next[k]!.t_ms === prev[0]!.t_ms && next[next.length - 1]!.t_ms === prev[prev.length - 1]!.t_ms
    ? k
    : 0;
}

export function ChartPane({ driver, index }: { driver: CursorDriver; index: number }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const view = new ChartView(ref.current!, timeAxis);
    let prev: readonly Bar[] = [];
    let prevKey = "";
    const stopData = effect(() => {
      const pane = driver.panes.value[index];
      const bars = pane?.bars ?? [];
      const key = pane === undefined ? "" : `${pane.symbol}|${pane.period}|${pane.rth}`;
      const same = key === prevKey;
      view.setRows(
        bars.map((b) => ({ x: b.t_ms, open: b.open, high: b.high, low: b.low, close: b.close })),
        same ? prependedCount(prev, bars) : 0,
        !same,
      );
      prev = bars;
      prevKey = key;
    });
    const stopRange = view.onVisibleRange((from, to) => driver.onVisibleRange(index, from, to));
    return () => {
      stopRange();
      stopData();
      view.dispose();
    };
  }, [driver, index]);

  return <div class="chart" ref={ref} />;
}
