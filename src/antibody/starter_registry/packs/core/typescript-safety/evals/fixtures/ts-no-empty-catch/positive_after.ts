export function readJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch (error) {}
  return null;
}
