/**
 * The horizontal axis a `ChartView` is built on. The view itself never knows
 * what x is; an axis supplies the chart factory and the x -> chart-space map.
 *
 * `timeAxis` is the only place in the client where a wire timestamp (Unix ms)
 * becomes a lightweight-charts `UTCTimestamp` (Unix seconds), and back. An
 * ingest once put a whole dataset in January 1970 through exactly this
 * confusion (ADR-023), so the conversion is kept to one visible pair.
 */

import {
  createChart,
  TickMarkType,
  type IChartApiBase,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

import type { EpochMs } from "../wire/types";

export interface Axis<X, H> {
  create(el: HTMLElement): IChartApiBase<H>;
  toHorz(x: X): H;
}

export function toChartTime(t: EpochMs): UTCTimestamp {
  return (t / 1000) as UTCTimestamp;
}

export function fromChartTime(t: Time): EpochMs {
  if (typeof t !== "number") {
    throw new TypeError(`expected a UTCTimestamp, got ${JSON.stringify(t)}`);
  }
  return (t * 1000) as EpochMs;
}

// The market's clock. lightweight-charts renders UTCTimestamp in UTC unless
// told otherwise, and a trading day is an America/New_York property.
const ET = "America/New_York";
const fmt = (o: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat("en-US", { timeZone: ET, hourCycle: "h23", ...o });

const fYear = fmt({ year: "numeric" });
const fMonth = fmt({ month: "short", year: "numeric" });
const fDay = fmt({ month: "short", day: "numeric" });
const fTime = fmt({ hour: "2-digit", minute: "2-digit" });
const fFull = fmt({
  weekday: "short",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

function tickMark(t: Time, type: TickMarkType): string {
  const d = fromChartTime(t);
  switch (type) {
    case TickMarkType.Year:
      return fYear.format(d);
    case TickMarkType.Month:
      return fMonth.format(d);
    case TickMarkType.DayOfMonth:
      return fDay.format(d);
    default:
      return fTime.format(d);
  }
}

export const timeAxis: Axis<EpochMs, Time> = {
  create: (el) =>
    createChart(el, {
      autoSize: true,
      localization: { timeFormatter: (t: Time) => `${fFull.format(fromChartTime(t))} ET` },
      timeScale: { timeVisible: true, tickMarkFormatter: tickMark },
    }),
  toHorz: toChartTime,
};
