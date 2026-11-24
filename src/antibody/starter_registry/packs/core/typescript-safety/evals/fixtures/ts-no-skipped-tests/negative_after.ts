import { add } from "./math";

describe("math.add", () => {
  it("adds numbers", () => {
    expect(add(1, 2)).toBe(3);
    expect(add(-1, 1)).toBe(0);
  });
});
