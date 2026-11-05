/**
 * Paging reassembles exactly (brief test 6). The fake transport serves a whole
 * fixture series with the route's paging contract -- the newest `limit` bars of
 * `[t0_ms, t1_ms]`, `has_more` when older ones were left out -- so what is
 * tested is the driver's stitching, not the server's grid. Whether the fake
 * matches the real route is checked against the live service, not here.
 */

import { describe, expect, it } from "vitest";

import { BrowseDriver } from "../src/drivers/browse";
import type { BarsQuery, Transport } from "../src/wire/client";
import type { Bar, EpochMs, Session } from "../src/wire/types";
import { barsOf, sessionsOf } from "./wire-fixtures";

import bars5mExt from "../../fixtures/bars_5m_extended_hours.json";
import bars5m from "../../fixtures/bars_1d_one_session_5m.json";
import sessJan from "./fixtures/sessions_2024_01_02_to_04.json";

function fakeTransport(full: readonly Bar[], sessions: readonly Session[]) {
  const calls: BarsQuery[] = [];
  const t: Transport = {
    async getBars(q) {
      calls.push(q);
      const inRange = full.filter((b) => b.t_ms >= q.t0_ms && b.t_ms <= q.t1_ms);
      const limit = q.limit ?? 5000;
      return { bars: inRange.slice(-limit), has_more: inRange.length > limit };
    },
    async getSessions(t0, t1) {
      return sessions.filter((s) => s.ext_close_ms >= t0 && s.ext_open_ms - 60_000 <= t1);
    },
    async getLabels() {
      return [];
    },
  };
  return { t, calls };
}

async function pageToEnd(d: BrowseDriver): Promise<number> {
  let pages = 1;
  while (d.load.value.hasMore) {
    await d.pageLeft();
    pages++;
  }
  return pages;
}

describe("BrowseDriver paging reassembles the series", () => {
  const full = barsOf(bars5mExt); // 191 bars, rth=false
  const sessions = sessionsOf(sessJan);
  const last = full[full.length - 1]!.t_ms;

  it.each([1, 7, 20, 190, 191, 5000])("page size %i", async (size) => {
    const { t, calls } = fakeTransport(full, sessions);
    const d = new BrowseDriver(t, size);
    await d.open({ symbol: "AAPL", period: "5m", rth: false }, last);
    const pages = await pageToEnd(d);

    const got = d.panes.value[0]!.bars;
    expect(got.map((b) => b.t_ms)).toEqual(full.map((b) => b.t_ms));
    expect(got).toEqual(full);
    expect(pages).toBe(Math.ceil(full.length / size));
    expect(calls.every((c) => c.period === "5m" && c.rth === false)).toBe(true);
  });

  it("asks for each older page with t1_ms one millisecond below the oldest bar", async () => {
    const { t, calls } = fakeTransport(full, sessions);
    const d = new BrowseDriver(t, 50);
    await d.open({ symbol: "AAPL", period: "5m", rth: false }, last);
    await pageToEnd(d);
    const oldestAfter = [full.length - 50, full.length - 100, full.length - 150];
    expect(calls.slice(1).map((c) => c.t1_ms)).toEqual(
      oldestAfter.map((i) => full[i]!.t_ms - 1),
    );
  });

  it("never changes the period to make a range fit", async () => {
    const { t, calls } = fakeTransport(full, sessions);
    const d = new BrowseDriver(t, 10);
    await d.open({ symbol: "AAPL", period: "5m", rth: false }, last);
    await pageToEnd(d);
    expect(new Set(calls.map((c) => c.period))).toEqual(new Set(["5m"]));
  });
});

describe("BrowseDriver drops responses for a view it has left", () => {
  it("a slow first open cannot overwrite a later one", async () => {
    const full = barsOf(bars5m);
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const base = fakeTransport(full, sessionsOf(sessJan)).t;
    const t: Transport = {
      ...base,
      async getBars(q) {
        if (q.symbol === "SLOW") await gate;
        return base.getBars(q);
      },
    };
    const d = new BrowseDriver(t, 5000);
    const last = full[full.length - 1]!.t_ms;
    const slow = d.open({ symbol: "SLOW", period: "5m", rth: true }, last);
    await d.open({ symbol: "AAPL", period: "5m", rth: true }, last);
    release();
    await slow;
    expect(d.panes.value[0]!.symbol).toBe("AAPL");
  });
});

it("keeps bars on screen and reports the error when only labels fail", async () => {
  const full = barsOf(bars5m);
  const base = fakeTransport(full, sessionsOf(sessJan)).t;
  const err = new Error("503 label store not configured");
  const t: Transport = { ...base, getLabels: () => Promise.reject(err) };
  const d = new BrowseDriver(t, 5000);
  await d.open({ symbol: "AAPL", period: "5m", rth: true }, full[full.length - 1]!.t_ms as EpochMs);
  expect(d.panes.value[0]!.bars.length).toBe(78);
  expect(d.load.value.error).toBe(err);
});
