import test from "node:test";
import assert from "node:assert/strict";
import { parseInterfaceRate } from "../src/interfaceRate.ts";

test("rate entry uses Gbit/s while persisted assumptions use Mbit/s", () => {
  assert.equal(parseInterfaceRate("50"), 50000);
  assert.equal(parseInterfaceRate("0.1"), 100);
});
test("clearing an entry restores automatic rate instead of a zero-cost interface", () => {
  assert.equal(parseInterfaceRate(""), null);
  assert.equal(parseInterfaceRate("  "), null);
});
test("invalid and nonfinite rates are rejected before saving", () => {
  for (const value of ["0","-1","invalid","Infinity","1e100"])
    assert.throws(() => parseInterfaceRate(value));
});
