export function readJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch (error) {
    console.error("invalid JSON", error);
    return null;
  }
}
