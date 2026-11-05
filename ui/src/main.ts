/**
 * Walking skeleton: one symbol, one period, the newest page of bars, drawn.
 * No paging, no labels, no chrome yet.
 */

import { timeAxis } from "./chart/axis";
import { ChartView } from "./chart/view";
import { getBars, ServiceError } from "./wire/client";
import type { EpochMs } from "./wire/types";

const el = document.getElementById("chart")!;
const status = document.getElementById("status")!;

async function main(): Promise<void> {
  const view = new ChartView(el, timeAxis);
  const symbol = "AAPL";
  const page = await getBars({
    symbol,
    period: "5m",
    rth: true,
    t0_ms: 0 as EpochMs,
    t1_ms: Date.now() as EpochMs,
    limit: 2000,
  });
  view.setRows(page.bars.map((b) => ({ x: b.t_ms, ...b })));
  status.textContent = `${symbol} 5m RTH: ${page.bars.length} bars, has_more=${page.has_more}`;
}

main().catch((e: unknown) => {
  status.textContent =
    e instanceof ServiceError ? `Service error ${e.status}: ${e.detail}` : `Error: ${String(e)}`;
  status.classList.add("error");
});
