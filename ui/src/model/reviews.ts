/**
 * Reviews as modifiers of their target (ADR-027). A review is a label with
 * `label_type="review"` and payload `{target_id, verdict, round}`; it is never
 * drawn itself, it decides how its target is drawn.
 */

import type { Label } from "../wire/types";

export type Verdict = "accept" | "reject";

export interface ReviewState {
  reviewId: number;
  verdict: Verdict;
  round: number | null;
}

export interface ResolvedReviews {
  /** target id -> the review with the highest id. */
  byTarget: ReadonlyMap<number, ReviewState>;
  /**
   * Reviews whose payload is not `{target_id: int, verdict: accept|reject}`. The
   * store accepts any payload, so these can exist. They are kept out of
   * `byTarget` and returned so the UI can say so: skipped silently, a malformed
   * latest review would let an older verdict show as current.
   */
  malformed: readonly Label[];
}

export const isReview = (l: Label): boolean => l.label_type === "review";

/**
 * Latest review per target. An orphan -- a review whose target was deleted --
 * lands here like any other; it draws nothing because no label looks it up.
 */
export function resolveReviews(labels: readonly Label[]): ResolvedReviews {
  const byTarget = new Map<number, ReviewState>();
  const malformed: Label[] = [];
  for (const l of labels) {
    if (!isReview(l)) continue;
    const { target_id, verdict, round } = l.payload;
    if (!Number.isInteger(target_id) || (verdict !== "accept" && verdict !== "reject")) {
      malformed.push(l);
      continue;
    }
    const prev = byTarget.get(target_id as number);
    if (prev === undefined || l.id > prev.reviewId) {
      byTarget.set(target_id as number, {
        reviewId: l.id,
        verdict,
        round: Number.isInteger(round) ? (round as number) : null,
      });
    }
  }
  return { byTarget, malformed };
}

export type Status = "human" | "pending" | "accepted" | "rejected";

export interface LabelStyle {
  /** Never changes with review: an accepted proposal keeps the model's colouring. */
  source: "human" | "model";
  status: Status;
  /** `payload.conf` of a model proposal when it is a finite number. */
  confidence: number | null;
}

/**
 * The style of every drawable label, keyed by id. Reviews are not drawable and
 * get no entry. A review only modifies a model proposal; one aimed at a human
 * label or at another review is ignored.
 */
export function styleLabels(
  labels: readonly Label[],
  reviews: ResolvedReviews,
): Map<number, LabelStyle> {
  const out = new Map<number, LabelStyle>();
  for (const l of labels) {
    if (isReview(l)) continue;
    if (l.source === "human") {
      out.set(l.id, { source: "human", status: "human", confidence: null });
      continue;
    }
    const conf = l.payload.conf;
    const r = reviews.byTarget.get(l.id);
    out.set(l.id, {
      source: "model",
      status: r === undefined ? "pending" : r.verdict === "accept" ? "accepted" : "rejected",
      confidence: typeof conf === "number" && Number.isFinite(conf) ? conf : null,
    });
  }
  return out;
}
