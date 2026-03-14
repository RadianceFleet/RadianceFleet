/**
 * Build a URLSearchParams from a record, skipping undefined/null values
 * and converting numbers/booleans to strings.
 */
export function buildQueryParams(
  params: Record<string, string | number | boolean | undefined | null>
): URLSearchParams {
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value == null || value === "") continue;
    sp.set(key, String(value));
  }
  return sp;
}
