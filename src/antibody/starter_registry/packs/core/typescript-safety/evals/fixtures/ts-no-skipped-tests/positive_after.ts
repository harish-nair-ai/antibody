import { add } from "./math";

describe("add", () => {
  it.skip("adds numbers", () => {
    expect(add(1, 2)).toBe(3);
    expect(add(-1, 1)).toBe(0);
  });
});
