export function readJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch (err) {
    console.err("invalid JSON", err);
    return null;
  }
}
