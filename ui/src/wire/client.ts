/**
 * Typed transport for the WiseTrade service (`api/`). One function per route;
 * payloads come back exactly as the routes return them.
 *
 * Every non-2xx answer is thrown as a `ServiceError` carrying the server's own
 * `detail`. A 503 in particular means the service is misconfigured (no bar
 * database, no label store) and must reach the user as such -- never as an
 * empty chart, which would read as "no data here".
 */

import type { Bar, EpochMs, Label, Period, Session } from "./types";

const API_BASE: string = import.meta.env.VITE_WISETRADE_API ?? "http://127.0.0.1:8000";

export class ServiceError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly url: string,
  ) {
    super(`${status} ${detail}`);
    this.name = "ServiceError";
  }
}

type Query = Record<string, string | number | boolean | undefined>;

async function getJson<T>(path: string, query: Query = {}): Promise<T> {
  const url = new URL(path, API_BASE);
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined) url.searchParams.set(k, String(v));
  }
  const res = await fetch(url);
  if (!res.ok) throw await toServiceError(res, url.toString());
  return (await res.json()) as T;
}

async function toServiceError(res: Response, url: string): Promise<ServiceError> {
  // FastAPI answers {"detail": str} for raised errors and {"detail": [...]} for
  // parameter validation; anything else is shown raw.
  const text = await res.text();
  let detail = text;
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
    else if (body.detail !== undefined) detail = JSON.stringify(body.detail);
  } catch {
    // not JSON; keep the raw text
  }
  return new ServiceError(res.status, detail, url);
}

export const getSymbols = (): Promise<string[]> => getJson("/symbols");

export const getSessions = (t0_ms: EpochMs, t1_ms: EpochMs): Promise<Session[]> =>
  getJson("/sessions", { t0_ms, t1_ms });

export interface BarsPage {
  bars: Bar[];
  /** Older buckets exist that this response omits (ADR-025). */
  has_more: boolean;
}

export interface BarsQuery {
  symbol: string;
  period: Period;
  t0_ms: EpochMs;
  t1_ms: EpochMs;
  rth: boolean;
  limit?: number;
}

export const getBars = (q: BarsQuery): Promise<BarsPage> => getJson("/bars", { ...q });

export interface LabelsQuery {
  symbol: string;
  t0_ms: EpochMs;
  t1_ms: EpochMs;
}

/**
 * Labels whose coverage overlaps `[t0_ms, t1_ms]`. Deliberately no other
 * filter: a `period`, `rth`, `source` or `label_type` filter can drop a
 * review's target while keeping the review, which then reads as an orphan.
 * The chart filters on the client, where both halves are in view.
 */
export const getLabels = (q: LabelsQuery): Promise<Label[]> => getJson("/labels", { ...q });

/** The routes a cursor driver reads. Tests substitute a fixture-backed one. */
export interface Transport {
  getBars(q: BarsQuery): Promise<BarsPage>;
  getSessions(t0_ms: EpochMs, t1_ms: EpochMs): Promise<Session[]>;
  getLabels(q: LabelsQuery): Promise<Label[]>;
}

export const http: Transport = { getBars, getSessions, getLabels };
