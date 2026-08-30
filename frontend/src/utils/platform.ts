/**
 * Client OS platform detection and representative path placeholder helper.
 */

export type ClientPlatform = 'windows' | 'macos' | 'linux';

/**
 * Detects the client operating system platform using modern Client Hints
 * (navigator.userAgentData.platform) with fallbacks to navigator.platform
 * and navigator.userAgent. Defaults to 'linux' if undetermined.
 */
export function detectClientPlatform(nav?: Navigator): ClientPlatform {
  const currentNav =
    nav || (typeof navigator !== 'undefined' ? navigator : undefined);

  if (!currentNav) {
    return 'linux';
  }

  // 1. User-Agent Client Hints (modern Chromium browsers)
  const uaDataPlatform = (currentNav as any).userAgentData?.platform?.toLowerCase();
  if (uaDataPlatform) {
    if (uaDataPlatform.includes('win')) return 'windows';
    if (uaDataPlatform.includes('mac')) return 'macos';
    if (uaDataPlatform.includes('linux') || uaDataPlatform.includes('x11')) return 'linux';
  }

  // 2. Standard navigator.platform & navigator.userAgent
  const platform = (currentNav.platform || '').toLowerCase();
  const ua = (currentNav.userAgent || '').toLowerCase();

  if (platform.includes('win') || ua.includes('windows') || ua.includes('win32') || ua.includes('win64')) {
    return 'windows';
  }
  if (platform.includes('mac') || ua.includes('macintosh') || ua.includes('mac os x')) {
    return 'macos';
  }
  if (platform.includes('linux') || ua.includes('linux') || ua.includes('x11')) {
    return 'linux';
  }

  return 'linux';
}

/**
 * Returns a representative local filesystem path placeholder formatted
 * according to the detected or specified client operating system.
 */
export function getOsPathPlaceholder(platform?: ClientPlatform): string {
  const resolved = platform || detectClientPlatform();
  switch (resolved) {
    case 'windows':
      return 'C:\\Users\\username\\Documents\\RecipeScans';
    case 'macos':
      return '/Users/username/Documents/RecipeScans';
    case 'linux':
    default:
      return '/home/username/recipes';
  }
}
