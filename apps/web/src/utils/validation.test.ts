import { describe, it, expect } from "vitest";
import { isLanguagePairValid } from "@/utils/validation";

describe("validation", () => {
  it("isLanguagePairValid rejects same source and target", () => {
    expect(isLanguagePairValid("en", "en")).toBe(false);
    expect(isLanguagePairValid("en", "hi")).toBe(true);
  });
});
