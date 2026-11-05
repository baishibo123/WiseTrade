import { describe, expect, it } from "vitest";

import { completeness } from "../src/model/completeness";
import { barsOf } from "./wire-fixtures";

import incomplete from "../../fixtures/bars_5m_incomplete_buckets.json";
import ext from "../../fixtures/bars_5m_extended_hours.json";

describe("completeness", () => {
  it("grades AKAM's incomplete buckets, down to n_src=1", () => {
    const cs = barsOf(incomplete).map(completeness);
    expect(cs.filter((c) => c !== null && c < 1).length).toBe(31);
    expect(Math.min(...(cs as number[]))).toBe(1 / 5);
  });

  it("is null, not 0, outside regular hours", () => {
    expect(barsOf(ext).map(completeness).every((c) => c === null)).toBe(true);
  });
});
