import { signal, useSignalEffect } from "@preact/signals";

import { BrowseDriver, type BrowseView } from "../drivers/browse";
import { getSymbols, http, ServiceError } from "../wire/client";
import { PERIODS, type EpochMs, type Period } from "../wire/types";
import { ChartPane } from "./ChartPane";

const driver = new BrowseDriver(http);
const view = signal<BrowseView>({ symbol: "AAPL", period: "5m", rth: true });
const symbols = signal<string[]>([]);
const symbolsError = signal<unknown>(null);

getSymbols().then(
  (s) => (symbols.value = s),
  (e: unknown) => (symbolsError.value = e),
);

function describeError(e: unknown): string {
  return e instanceof ServiceError ? `Service error ${e.status}: ${e.detail}` : `Error: ${String(e)}`;
}

function Status() {
  const { loading, hasMore, error } = driver.load.value;
  const pane = driver.panes.value[0];
  const err = error ?? symbolsError.value;
  return (
    <span class="status">
      {pane !== undefined && `${pane.bars.length} bars · ${pane.labels.length} labels`}
      {hasMore && " · older bars exist"}
      {loading && " · loading…"}
      {err !== null && <span class="error"> · {describeError(err)}</span>}
    </span>
  );
}

export function App() {
  useSignalEffect(() => {
    void driver.open(view.value, Date.now() as EpochMs);
  });
  const v = view.value;
  const set = (patch: Partial<BrowseView>) => (view.value = { ...v, ...patch });
  return (
    <>
      <header>
        <select
          value={v.symbol}
          onChange={(e) => set({ symbol: e.currentTarget.value })}
          aria-label="Symbol"
        >
          {(symbols.value.length > 0 ? symbols.value : [v.symbol]).map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          value={v.period}
          onChange={(e) => set({ period: e.currentTarget.value as Period })}
          aria-label="Period"
        >
          {PERIODS.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <label>
          <input
            type="checkbox"
            checked={v.rth}
            onChange={(e) => set({ rth: e.currentTarget.checked })}
          />
          RTH only
        </label>
        <Status />
      </header>
      <ChartPane driver={driver} index={0} />
    </>
  );
}
