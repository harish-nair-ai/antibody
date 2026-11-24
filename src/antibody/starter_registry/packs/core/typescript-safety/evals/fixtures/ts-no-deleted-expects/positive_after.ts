import { add } from "./math";

describe("add", () => {
  it("adds numbers", () => {
    expect(add(1, 2)).toBe(3);
  });
});
