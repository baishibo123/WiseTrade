/**
 * Browse: the user moves the cursor by panning. The newest page up to an anchor
 * is loaded first; panning near the left edge pages in older bars.
 *
 * Paging relies on ADR-025: the service returns the NEWEST `limit` buckets of a
 * range, and pages partition the grid by bucket end, so refetching with
 * `t1_ms = oldest.t_ms - 1` reassembles the series with no hole and no
 * duplicate. The period is the user's choice and is never changed to make a
 * range fit -- a longer history is more pages, not coarser bars.
 */

import { signal, type ReadonlySignal } from "@preact/signals";

import { PERIOD_MINUTES } from "../model/grid";
import type { PaneState } from "../model/pane";
import type { Transport } from "../wire/client";
import type { Bar, EpochMs, Label, Period, Session } from "../wire/types";
import type { CursorDriver, OverlayLayer } from "./driver";

export interface BrowseView {
  symbol: string;
  period: Period;
  rth: boolean;
}

export interface LoadState {
  loading: boolean;
  /** Older bars exist that are not loaded. */
  hasMore: boolean;
  /** Shown to the user as-is: a 503 is a misconfigured service, not "no data". */
  error: unknown;
}

/** Start paging when fewer than this many loaded bars lie left of the view. */
const PREFETCH_BARS = 200;

const EPOCH = 0 as EpochMs;

export class BrowseDriver implements CursorDriver {
  private readonly _panes = signal<readonly PaneState[]>([]);
  private readonly _load = signal<LoadState>({ loading: false, hasMore: false, error: null });
  readonly panes: ReadonlySignal<readonly PaneState[]> = this._panes;
  readonly load: ReadonlySignal<LoadState> = this._load;
  readonly layers: ReadonlySignal<readonly OverlayLayer[]> = signal([]);

  /** Bumped by every `open`; a response from an older generation is dropped. */
  private gen = 0;
  private view: BrowseView | null = null;
  private sessions: readonly Session[] = [];

  constructor(
    private readonly transport: Transport,
    private readonly pageSize = 2000,
  ) {}

  /** Load the newest page ending at or before `anchor_ms`, replacing what is shown. */
  async open(view: BrowseView, anchor_ms: EpochMs): Promise<void> {
    const g = ++this.gen;
    this.view = view;
    this.sessions = [];
    this._panes.value = [];
    this._load.value = { loading: true, hasMore: false, error: null };
    await this.fetchPage(g, anchor_ms, []);
  }

  /** Prepend the next older page. No-op while loading or when nothing older exists. */
  async pageLeft(): Promise<void> {
    const pane = this._panes.value[0];
    const { loading, hasMore } = this._load.value;
    if (pane === undefined || loading || !hasMore || pane.bars.length === 0) return;
    const g = this.gen;
    this._load.value = { ...this._load.value, loading: true };
    await this.fetchPage(g, (pane.bars[0]!.t_ms - 1) as EpochMs, pane.bars);
  }

  onVisibleRange(_paneIndex: number, from: number): void {
    if (from < PREFETCH_BARS) void this.pageLeft();
  }

  dispose(): void {
    this.gen++;
  }

  private async fetchPage(g: number, t1_ms: EpochMs, newer: readonly Bar[]): Promise<void> {
    const v = this.view!;
    try {
      const page = await this.transport.getBars({
        symbol: v.symbol,
        period: v.period,
        rth: v.rth,
        t0_ms: EPOCH,
        t1_ms,
        limit: this.pageSize,
      });
      if (g !== this.gen) return;
      const bars = page.bars.length === 0 ? newer : [...page.bars, ...newer];
      if (page.bars.length > 0) {
        const fresh = await this.transport.getSessions(
          page.bars[0]!.t_ms,
          page.bars[page.bars.length - 1]!.t_ms,
        );
        if (g !== this.gen) return;
        this.sessions = mergeSessions(fresh, this.sessions);
      }
      // Bars stay on screen when only the label store fails; its error is shown.
      let labels: readonly Label[] = [];
      let error: unknown = null;
      if (bars.length > 0) {
        try {
          // The first bucket covers minutes before its own t_ms; over-fetching
          // is harmless, under-fetching drops a label at the left edge.
          labels = await this.transport.getLabels({
            symbol: v.symbol,
            t0_ms: (bars[0]!.t_ms - PERIOD_MINUTES[v.period] * 60_000) as EpochMs,
            t1_ms: bars[bars.length - 1]!.t_ms,
          });
        } catch (e) {
          error = e;
        }
        if (g !== this.gen) return;
      }
      this._panes.value = [{ ...v, bars, sessions: this.sessions, labels, cursor_ms: null }];
      // An empty page ends paging whatever has_more says: there is no older
      // bar to anchor the next request on.
      this._load.value = {
        loading: false,
        hasMore: page.bars.length > 0 && page.has_more,
        error,
      };
    } catch (error) {
      if (g !== this.gen) return;
      this._load.value = { ...this._load.value, loading: false, error };
    }
  }
}

/** Union by `et_date`, ascending. Adjacent pages share the session they meet in. */
function mergeSessions(a: readonly Session[], b: readonly Session[]): Session[] {
  const byDate = new Map<string, Session>();
  for (const s of [...a, ...b]) byDate.set(s.et_date, s);
  return [...byDate.values()].sort((x, y) => x.rth_open_ms - y.rth_open_ms);
}
