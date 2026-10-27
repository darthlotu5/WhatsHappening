export function eventDate(value: number | string | null, label?: string): string {
  if (value === null || value === "") return label ? `${label} (year unconfirmed)` : "Date to be announced";
  const timestamp = Number(value);
  if (!Number.isFinite(timestamp)) return "Date to be announced";
  return new Date(timestamp * 1000).toDateString();
}
