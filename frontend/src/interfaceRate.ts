export function parseInterfaceRate(text: string): number | null {
  if (!text.trim()) return null;
  const gbps = Number(text);
  if (!Number.isFinite(gbps) || gbps <= 0 || gbps > 1_000_000)
    throw new Error("速率需大于 0 且不超过 1,000,000 Gbit/s；留空可恢复自动。");
  return gbps * 1000;
}
