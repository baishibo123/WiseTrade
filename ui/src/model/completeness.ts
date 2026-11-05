import type { Bar } from "../wire/types";

/**
 * Fraction of a bucket's minutes that had source bars: `n_src / n_expected`.
 *
 * Graded rather than a flag, because on thin names most intraday buckets are
 * short by a minute or two; a binary mark would outline nearly every bar and
 * hide the few built from a handful of minutes. `null` means unknown -- outside
 * regular hours no theoretical count exists -- and must never be drawn as 0.
 * Whether it is drawn at all is a view switch, not a property of the bar.
 */
export function completeness(b: Bar): number | null {
  return b.n_expected === null ? null : b.n_src / b.n_expected;
}
