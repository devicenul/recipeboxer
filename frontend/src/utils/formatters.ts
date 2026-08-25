/**
 * Formats a duration in seconds into a human-readable string (e.g. "2m 15s" or "45s").
 */
export function formatDuration(seconds: number): string {
  if (isNaN(seconds) || seconds <= 0) return "0s";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  if (mins === 0) return `${secs}s`;
  return `${mins}m ${secs.toString().padStart(2, "0")}s`;
}

/**
 * Formats an ISO datetime string into a localized short date/time.
 */
export function formatDate(isoString?: string): string {
  if (!isoString) return "N/A";
  try {
    const date = new Date(isoString);
    if (isNaN(date.getTime())) return isoString;
    return date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return isoString;
  }
}

/**
 * Formats a decimal score (0.0 - 1.0) into a percentage string (e.g. "94%").
 */
export function formatScorePercent(score?: number): string {
  if (score === undefined || isNaN(score)) return "0%";
  return `${Math.round(score * 100)}%`;
}

/**
 * Converts a human-readable duration string into ISO 8601 format (e.g. "45 mins" -> "PT45M", "1 hr 15 mins" -> "PT1H15M").
 */
export function toISO8601Duration(text?: string): string {
  if (!text || !text.trim()) return "PT0M";
  const t = text.trim();

  // If already ISO 8601 duration
  if (/^P(T|\d)/i.test(t)) {
    return t.toUpperCase();
  }

  let hours = 0;
  let minutes = 0;

  const hrMatch = t.match(/(\d+)\s*(?:h|hr|hrs|hour|hours)/i);
  if (hrMatch) {
    hours = parseInt(hrMatch[1], 10);
  }

  const minMatch = t.match(/(\d+)\s*(?:m|min|mins|minute|minutes)/i);
  if (minMatch) {
    minutes = parseInt(minMatch[1], 10);
  }

  // If only a plain number was passed, assume minutes
  if (!hrMatch && !minMatch) {
    const plainNum = parseInt(t, 10);
    if (!isNaN(plainNum)) {
      minutes = plainNum;
    }
  }

  if (hours > 0 && minutes > 0) {
    return `PT${hours}H${minutes}M`;
  } else if (hours > 0) {
    return `PT${hours}H`;
  } else if (minutes > 0) {
    return `PT${minutes}M`;
  }

  return "PT0M";
}
