/**
 * Frontend validation helpers.
 */

const SUPPORTED_LANGUAGES = new Set(["en", "hi", "bn"]);

export function isLanguageCode(code: string): boolean {
  return SUPPORTED_LANGUAGES.has(code);
}

export function isLanguagePairValid(speak: string, hear: string): boolean {
  if (!isLanguageCode(speak) || !isLanguageCode(hear)) return false;
  // User can't speak and hear the same language — no point in translating.
  return speak !== hear;
}

export interface CallCreateInput {
  callee_id: string;
  source_language: string;
  target_language: string;
}

export function validateCallCreate(input: CallCreateInput): string | null {
  if (!input.callee_id || input.callee_id.trim().length < 3) {
    return "callee_id is required (min 3 chars)";
  }
  if (!isLanguagePairValid(input.source_language, input.target_language)) {
    return "source_language and target_language must be different supported codes";
  }
  return null;
}
