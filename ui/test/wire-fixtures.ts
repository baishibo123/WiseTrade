/**
 * Fixture JSON is typed by inference, with plain `number` stamps; the client
 * sees wire types with `EpochMs`. These casts are the test-side counterpart of
 * the decode step in the real transport.
 */

import type { Bar, Label, Session } from "../src/wire/types";

export const barsOf = (f: { response: { bars: unknown } }) => f.response.bars as Bar[];
export const sessionsOf = (f: { response: unknown }) => f.response as Session[];
export const labelsOf = (f: { response: unknown }) => f.response as Label[];
