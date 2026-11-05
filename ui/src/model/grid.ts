/**
 * Where a bucket starts. A port of the lower half of
 * `database/resample.py:bucket_span` -- the third implementation of that
 * formula, after the Python and the SQL in the same module.
 *
 * The chart needs it because the projection rule marks a bar when the bar's own
 * bucket `(start, t_ms]` intersects a label's coverage, and `Bar` carries only
 * the end. On a regular-hours chart `t_ms - n_expected` minutes gives the same
 * answer, but `n_expected` is null outside regular hours, so the formula is
 * ported once and used for both.
 *
 * The clamp at the session open is not optional. `"1d"` is 1440 minutes on
 * purpose, longer than any session (ADR-025), and only the clamp folds it into
 * one session: unclamped, the 1d bar of a half day reaches back to 13:00 the
 * previous day and picks up that day's last three hours of labels.
 */

import type { EpochMs, Period, SessionOpen } from "../wire/types";

const MS_PER_MIN = 60_000;

/**
 * A session as the grid reads it: a whole `Session` in Browse, or the
 * `SessionOpen` a blind view is limited to. `ext_close_ms`, when present, only
 * tightens a check.
 */
export type GridSession = SessionOpen & { ext_close_ms?: EpochMs };

export const PERIOD_MINUTES: Readonly<Record<Period, number>> = {
  "1m": 1,
  "2m": 2,
  "5m": 5,
  "15m": 15,
  "30m": 30,
  "1h": 60,
  "2h": 120,
  "1d": 1440,
};

/**
 * The session's exclusive lower bound, as `resample.py:session_bounds` has it.
 * `ext_open_ms` is the eob of the first observed bar, so that bar opens one
 * minute earlier.
 */
export function sessionLo(s: SessionOpen, rth: boolean): EpochMs {
  return (rth ? s.rth_open_ms : s.ext_open_ms - MS_PER_MIN) as EpochMs;
}

/**
 * The session a bucket end belongs to: the last one whose lower bound is below
 * it. Only the open side is read, so this works on the `SessionOpen` a blind
 * view is limited to.
 *
 * `sessions` must be a whole `/sessions` response, ascending, for a range that
 * contains `t_ms`. A session missing from the middle would silently resolve to
 * the one before it; the upper-bound check below catches that for regular
 * hours, and for extended hours whenever `ext_close_ms` is available.
 */
export function sessionOf(
  t_ms: EpochMs,
  sessions: readonly GridSession[],
  rth: boolean,
): SessionOpen {
  let lo = 0;
  let hi = sessions.length; // first index whose lower bound is >= t_ms
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (sessionLo(sessions[mid]!, rth) < t_ms) lo = mid + 1;
    else hi = mid;
  }
  const s = sessions[lo - 1];
  if (s === undefined) {
    throw new RangeError(`${t_ms} precedes every supplied session (rth=${rth})`);
  }
  const upper = rth ? s.rth_close_ms : s.ext_close_ms;
  if (upper !== undefined && t_ms > upper) {
    throw new RangeError(
      `${t_ms} is after session ${s.et_date} closes (rth=${rth}) and before the next ` +
        `supplied session opens; a bucket end lies inside a session`,
    );
  }
  return s;
}

/** A bucket's exclusive start: `max(session lower bound, t_ms - period)`. */
export function bucketStart(
  t_ms: EpochMs,
  period: Period,
  rth: boolean,
  sessions: readonly GridSession[],
): EpochMs {
  const s = sessionOf(t_ms, sessions, rth);
  return Math.max(sessionLo(s, rth), t_ms - PERIOD_MINUTES[period] * MS_PER_MIN) as EpochMs;
}
