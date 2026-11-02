/**
 * Wire payloads, as frozen in docs/interfaces.md. Field names match the JSON
 * exactly; nothing here renames or converts.
 */

/**
 * Unix milliseconds UTC. Branded so that a seconds value -- what
 * lightweight-charts calls `UTCTimestamp` -- cannot be passed where a wire
 * timestamp is expected, or the reverse, without a visible cast (ADR-023).
 */
export type EpochMs = number & { readonly __unit: "ms" };

export const PERIODS = ["1m", "2m", "5m", "15m", "30m", "1h", "2h", "1d"] as const;
export type Period = (typeof PERIODS)[number];

export interface Bar {
  t_ms: EpochMs; // bucket END (eob)
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  n_src: number;
  n_expected: number | null; // null outside regular hours: unknown, not incomplete
}

export interface Session {
  et_date: string;
  ext_open_ms: EpochMs; // eob of the session's first observed bar
  rth_open_ms: EpochMs;
  rth_close_ms: EpochMs;
  ext_close_ms: EpochMs; // last observed bar: derived from data after any replay cursor
  n_bars_rth: number; // ingest-time count: also derived from data after the cursor
  is_half_day: boolean;
}

/**
 * The fields of a session known before it ends. A view that must not see the
 * future (blind labelling) is given this, never a whole `Session`.
 */
export type SessionOpen = Pick<
  Session,
  "et_date" | "ext_open_ms" | "rth_open_ms" | "rth_close_ms" | "is_half_day"
>;

export interface Label {
  id: number;
  symbol: string;
  anchor: "point" | "interval";
  t_from_ms: EpochMs;
  t_to_ms: EpochMs | null;
  period: Period;
  rth: boolean;
  label_type: string;
  payload: Record<string, unknown>;
  source: "human" | "model";
  created_at_ms: EpochMs;
  cover_from_ms: EpochMs; // exclusive
  cover_to_ms: EpochMs; // inclusive
  within_rth: boolean;
}
