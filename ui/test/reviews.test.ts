/**
 * Review styling on `labels_day_all`, case by case as the fixture's note
 * enumerates it: #6 accepted, #7 rejected, #8 pending, #9 rejected in round 1
 * then accepted in round 2, and the review of #10 an orphan that draws nothing.
 */

import { describe, expect, it } from "vitest";

import { resolveReviews, styleLabels } from "../src/model/reviews";
import type { Label } from "../src/wire/types";
import { labelsOf } from "./wire-fixtures";

import labelsDayAll from "../../fixtures/labels/labels_day_all.json";

const all = labelsOf(labelsDayAll);
const reviews = resolveReviews(all);
const styles = styleLabels(all, reviews);

describe("styleLabels on labels_day_all", () => {
  it("#6 accepted keeps the model's provenance", () => {
    expect(styles.get(6)).toEqual({ source: "model", status: "accepted", confidence: 0.91 });
  });

  it("#7 rejected", () => {
    expect(styles.get(7)).toEqual({ source: "model", status: "rejected", confidence: 0.74 });
  });

  it("#8 pending shows its confidence", () => {
    expect(styles.get(8)).toEqual({ source: "model", status: "pending", confidence: 0.68 });
  });

  it("#9: the round-2 accept (#14) beats the round-1 reject (#13)", () => {
    expect(reviews.byTarget.get(9)).toEqual({ reviewId: 14, verdict: "accept", round: 2 });
    expect(styles.get(9)).toEqual({ source: "model", status: "accepted", confidence: 0.66 });
  });

  it("the orphan review of #10 styles nothing", () => {
    expect(all.some((l) => l.id === 10)).toBe(false);
    expect(reviews.byTarget.get(10)?.reviewId).toBe(15);
    expect(styles.has(10)).toBe(false);
    expect(styles.has(15)).toBe(false);
  });

  it("human labels are styled as human, never pooled with proposals", () => {
    for (const id of [1, 2, 3, 4, 5]) {
      expect(styles.get(id)).toEqual({ source: "human", status: "human", confidence: null });
    }
  });

  it("no review gets a style of its own", () => {
    const reviewIds = all.filter((l) => l.label_type === "review").map((l) => l.id);
    expect(reviewIds).toEqual([11, 12, 13, 14, 15]);
    for (const id of reviewIds) expect(styles.has(id)).toBe(false);
    expect([...styles.keys()].sort((a, b) => a - b)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("nothing in the fixture is malformed", () => {
    expect(reviews.malformed).toEqual([]);
  });
});

describe("resolveReviews", () => {
  const base = all.find((l) => l.id === 13)!;
  const review = (id: number, payload: Record<string, unknown>): Label => ({ ...base, id, payload });

  it("orders by id, not by list position", () => {
    const r = resolveReviews([
      review(20, { target_id: 9, verdict: "accept", round: 2 }),
      review(19, { target_id: 9, verdict: "reject", round: 1 }),
    ]);
    expect(r.byTarget.get(9)?.verdict).toBe("accept");
  });

  it("reports a malformed review instead of letting an older verdict stand", () => {
    const bad = review(21, { target_id: 9, verdict: "maybe" });
    const r = resolveReviews([review(19, { target_id: 9, verdict: "reject" }), bad]);
    expect(r.malformed).toEqual([bad]);
  });
});
