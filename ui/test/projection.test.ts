/**
 * Projection of `labels_day_all` onto charts of other periods. Expected indexes
 * are hand-computed from the stamps (5m bar i ends at 09:30 + 5(i+1) min), not
 * re-derived with the rule under test.
 */

import { describe, expect, it } from "vitest";

import type { PaneState } from "../src/model/pane";
import { projectLabels, type LabelMark } from "../src/model/projection";
import { resolveReviews, styleLabels } from "../src/model/reviews";
import type { Bar, Period } from "../src/wire/types";
import { barsOf, labelsOf, sessionsOf } from "./wire-fixtures";

import bars5m from "../../fixtures/bars_1d_one_session_5m.json";
import bars5mExt from "../../fixtures/bars_5m_extended_hours.json";
import labelsDayAll from "../../fixtures/labels/labels_day_all.json";
import sessJan from "./fixtures/sessions_2024_01_02_to_04.json";

const labels = labelsOf(labelsDayAll);
const sessions = sessionsOf(sessJan);
const styles = styleLabels(labels, resolveReviews(labels));

function pane(bars: readonly Bar[], period: Period, rth: boolean): PaneState {
  return { symbol: "AAPL", period, rth, bars, sessions, labels, cursor_ms: null };
}

const spans = (ms: LabelMark[]) =>
  Object.fromEntries(ms.map((m) => [m.labelId, [m.i0, m.i1]]));

describe("5m RTH chart, one full session", () => {
  const bars = barsOf(bars5m);
  const marks = projectLabels(pane(bars, "5m", true), styles);

  it("marks each label on the bars its coverage touches", () => {
    expect(spans(marks)).toEqual({
      1: [10, 14], // 5m interval 10:25..10:45
      2: [15, 15], // 5m point 10:50
      3: [40, 40], // 1m point 12:51 -> the 5m bar ending 12:55
      4: [0, 77], //  1d point 16:00 -> the whole session
      6: [30, 33],
      7: [40, 42],
      8: [50, 53],
      9: [60, 62],
    });
  });

  it("hides #5 (within_rth=false) rather than clipping it", () => {
    expect(marks.some((m) => m.labelId === 5)).toBe(false);
  });

  it("draws no review", () => {
    expect(marks.some((m) => m.labelId >= 11)).toBe(false);
  });

  it("flags nothing as clipped when the window holds the whole session", () => {
    expect(marks.every((m) => !m.clipL && !m.clipR)).toBe(true);
  });
});

describe("1d RTH chart", () => {
  // The one 1d RTH bar of 2024-01-02 ends at the regular close.
  const day = { ...barsOf(bars5m)[0]!, t_ms: sessions[0]!.rth_close_ms, n_expected: 390 };
  const marks = projectLabels(pane([day], "1d", true), styles);

  it("puts every visible label on the one bar, intraday labels included", () => {
    expect(spans(marks)).toEqual({
      1: [0, 0], 2: [0, 0], 3: [0, 0], 4: [0, 0], 6: [0, 0], 7: [0, 0], 8: [0, 0], 9: [0, 0],
    });
  });
});

describe("5m extended-hours chart", () => {
  const bars = barsOf(bars5mExt);
  const marks = projectLabels(pane(bars, "5m", false), styles);

  it("shows #5 on exactly the 5m bars ending in (09:00, 10:00]", () => {
    const l5 = labels.find((l) => l.id === 5)!;
    const expected = bars
      .map((b, i) => [b.t_ms, i] as const)
      .filter(([t]) => t > l5.cover_from_ms && t <= l5.cover_to_ms)
      .map(([, i]) => i);
    expect(expected.length).toBe(12);
    const m = marks.find((x) => x.labelId === 5)!;
    expect([m.i0, m.i1]).toEqual([expected[0], expected[expected.length - 1]]);
  });
});

describe("window edges", () => {
  const bars = barsOf(bars5m).slice(12, 51); // bars 12..50

  it("clips a label that starts before the window", () => {
    const m = projectLabels(pane(bars, "5m", true), styles).find((x) => x.labelId === 1)!;
    expect([m.i0, m.i1, m.clipL, m.clipR]).toEqual([0, 2, true, false]);
  });

  it("clips a label that runs past both ends", () => {
    const m = projectLabels(pane(bars, "5m", true), styles).find((x) => x.labelId === 4)!;
    expect([m.i0, m.i1, m.clipL, m.clipR]).toEqual([0, bars.length - 1, true, true]);
  });

  it("gives no mark to a label whose coverage misses every loaded bar", () => {
    const window = barsOf(bars5m).slice(20, 30);
    const ids = projectLabels(pane(window, "5m", true), styles).map((m) => m.labelId);
    expect(ids).not.toContain(1);
    expect(ids).not.toContain(9);
  });
});

it("an empty pane has no marks", () => {
  expect(projectLabels(pane([], "5m", true), styles)).toEqual([]);
});
