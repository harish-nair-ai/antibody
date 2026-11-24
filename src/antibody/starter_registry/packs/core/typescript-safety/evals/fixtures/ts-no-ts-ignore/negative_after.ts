// Parse a decimal TCP port number.
export function parsePort(raw: string): number {
  return Number.parseInt(raw, 10);
}
