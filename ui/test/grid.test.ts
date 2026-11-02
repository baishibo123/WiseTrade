/**
 * `bucketStart` is checked against two numbers the server computed with the
 * real `bucket_span`, never against a re-derivation of the formula:
 *
 *   - every regular-hours bar's `n_expected` is its clamped span in minutes;
 *   - every label's `cover_from_ms` is the clamped start of its first bucket,
 *     on the label's own period and `rth`.
 */

import { describe, expect, it } from "vitest";

import { bucketStart, sessionOf } from "../src/model/grid";
import type { Bar, EpochMs, Label, Period, Session } from "../src/wire/types";

import bars1d5m from "../../fixtures/bars_1d_one_session_5m.json";
import bars1hShort from "../../fixtures/bars_1h_short_first_bucket.json";
import bars2hShort from "../../fixtures/bars_2h_short_first_bucket.json";
import bars1hHalf from "../../fixtures/bars_1h_half_day.json";
import bars2hHalf from "../../fixtures/bars_2h_half_day.json";
import bars5mIncomplete from "../../fixtures/bars_5m_incomplete_buckets.json";
import bars1dGoog from "../../fixtures/bars_1d_goog_missing_session.json";
import sessHalf from "../../fixtures/sessions_half_day.json";
import sessGoogWeek from "../../fixtures/sessions_week_around_goog_gap.json";
import labelsDayAll from "../../fixtures/labels/labels_day_all.json";
import labelsMorning from "../../fixtures/labels/labels_morning_window.json";
import sessJan from "./fixtures/sessions_2024_01_02_to_04.json";

// Fixture JSON is typed by inference; the wire types are what the client sees.
const sessions = (f: { response: unknown }) => f.response as Session[];
const bars = (f: { response: { bars: unknown } }) => f.response.bars as Bar[];
const labels = (f: { response: unknown }) => f.response as Label[];

const MIN = 60_000;

describe("bucketStart reproduces the server's n_expected on every RTH bar", () => {
  const cases: [string, Bar[], Period, Session[]][] = [
    ["5m full session", bars(bars1d5m), "5m", sessions(sessJan)],
    ["1h short first bucket", bars(bars1hShort), "1h", sessions(sessJan)],
    ["2h short first bucket", bars(bars2hShort), "2h", sessions(sessJan)],
    ["1h half day", bars(bars1hHalf), "1h", sessions(sessHalf)],
    ["2h half day", bars(bars2hHalf), "2h", sessions(sessHalf)],
    ["5m incomplete buckets", bars(bars5mIncomplete), "5m", sessions(sessJan)],
    ["1d across a missing session", bars(bars1dGoog), "1d", sessions(sessGoogWeek)],
  ];

  it.each(cases)("%s", (_name, bs, period, ss) => {
    expect(bs.length).toBeGreaterThan(0);
    for (const b of bs) {
      expect(b.n_expected).not.toBeNull();
      expect((b.t_ms - bucketStart(b.t_ms, period, true, ss)) / MIN).toBe(b.n_expected);
    }
  });
});

describe("bucketStart reproduces the store's cover_from_ms on every label", () => {
  const cases: [string, Label[]][] = [
    ["labels_day_all", labels(labelsDayAll)],
    ["labels_morning_window", labels(labelsMorning)],
  ];

  it.each(cases)("%s", (_name, ls) => {
    expect(ls.length).toBeGreaterThan(0);
    for (const l of ls) {
      expect(bucketStart(l.t_from_ms, l.period, l.rth, sessions(sessJan))).toBe(
        l.cover_from_ms,
      );
    }
  });

  it("covers both regimes and every period in the fixture", () => {
    const ls = labels(labelsDayAll);
    expect(new Set(ls.map((l) => l.period))).toEqual(new Set(["1m", "5m", "1h", "1d"]));
    expect(ls.some((l) => !l.rth)).toBe(true);
  });
});

describe("the session clamp", () => {
  const [half] = sessions(sessHalf);

  it("folds a half day's 1d bar into its own session", () => {
    // Unclamped, 13:00 minus 1440 minutes is 13:00 the previous day.
    const start = bucketStart(half!.rth_close_ms, "1d", true, [half!]);
    expect(start).toBe(half!.rth_open_ms);
    expect((half!.rth_close_ms - start) / MIN).toBe(210);
  });

  it("uses one minute before ext_open_ms as the extended-hours lower bound", () => {
    // resample.py:session_bounds: ext_open_ms is the first bar's eob.
    const [jan2] = sessions(sessJan);
    const start = bucketStart(jan2!.ext_close_ms, "1d", false, [jan2!]);
    expect(start).toBe(jan2!.ext_open_ms - MIN);
  });
});

describe("sessionOf refuses a stamp outside every session", () => {
  const ss = sessions(sessJan);

  it("before the first session", () => {
    expect(() => sessionOf((ss[0]!.rth_open_ms - MIN) as EpochMs, ss, true)).toThrow(RangeError);
  });

  it("in the overnight gap, when a regular-hours chart is asked", () => {
    expect(() => sessionOf((ss[0]!.rth_close_ms + MIN) as EpochMs, ss, true)).toThrow(RangeError);
  });

  it("in the gap left by a session missing from the list", () => {
    const withHole = [ss[0]!, ss[2]!];
    expect(() => sessionOf(ss[1]!.rth_close_ms, withHole, true)).toThrow(RangeError);
  });
});
