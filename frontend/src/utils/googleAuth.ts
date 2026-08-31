/**
 * Shared Google Identity Services (GIS) OAuth 2.0 Token Client Helper
 */

export const GOOGLE_CLIENT_ID_STORAGE_KEY = 'recipeboxer_google_client_id';

export interface GisTokenResponse {
  access_token?: string;
  error?: string;
  error_description?: string;
  error_uri?: string;
  expires_in?: number;
  scope?: string;
  token_type?: string;
}

export interface GisTokenClient {
  requestAccessToken: (overrideConfig?: { prompt?: string; hint?: string }) => void;
}

export interface GisTokenClientConfig {
  client_id: string;
  scope: string;
  callback: (response: GisTokenResponse) => void;
  error_callback?: (error: { type: string; message: string }) => void;
  prompt?: string;
  hint?: string;
}

declare global {
  interface Window {
    google?: {
      accounts?: {
        oauth2?: {
          initTokenClient: (config: GisTokenClientConfig) => GisTokenClient;
          hasGrantedAllScopes?: (tokenResponse: GisTokenResponse, firstScope: string, ...restScopes: string[]) => boolean;
          hasGrantedAnyScope?: (tokenResponse: GisTokenResponse, firstScope: string, ...restScopes: string[]) => boolean;
          revoke?: (accessToken: string, done?: () => void) => void;
        };
      };
    };
  }
}

export const GOOGLE_SCOPES = {
  DOCS_EXPORT: 'https://www.googleapis.com/auth/documents https://www.googleapis.com/auth/drive.file',
  PHOTOS_INGEST: 'https://www.googleapis.com/auth/photoslibrary.readonly',
  DRIVE_INGEST: 'https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/drive.metadata.readonly',
};

export interface GoogleAuthOptions {
  clientId?: string;
  scope: string;
  prompt?: string;
  hint?: string;
}

export function isValidClientIdString(val: unknown): boolean {
  if (typeof val !== 'string') return false;
  const trimmed = val.trim().replace(/^["']|["']$/g, '').trim();
  if (!trimmed) return false;
  const lower = trimmed.toLowerCase();
  if (
    lower === 'undefined' ||
    lower === 'null' ||
    lower === 'false' ||
    lower === 'none' ||
    lower === '[object object]'
  ) {
    return false;
  }
  return true;
}

export function getStoredGoogleClientId(): string {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      const stored =
        window.localStorage.getItem(GOOGLE_CLIENT_ID_STORAGE_KEY) ||
        window.localStorage.getItem('google_client_id') ||
        '';
      if (isValidClientIdString(stored)) {
        return stored.trim().replace(/^["']|["']$/g, '').trim();
      }
    }
  } catch {
    // Sandbox or restricted iframe environment fallback
  }
  return '';
}

export function setStoredGoogleClientId(clientId: string): void {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      const cleaned = (clientId || '').trim().replace(/^["']|["']$/g, '').trim();
      if (isValidClientIdString(cleaned)) {
        window.localStorage.setItem(GOOGLE_CLIENT_ID_STORAGE_KEY, cleaned);
        window.localStorage.removeItem('google_client_id');
      } else {
        window.localStorage.removeItem(GOOGLE_CLIENT_ID_STORAGE_KEY);
        window.localStorage.removeItem('google_client_id');
      }
    }
  } catch {
    // Sandbox or restricted iframe environment fallback
  }
}

export function clearStoredGoogleClientId(): void {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.removeItem(GOOGLE_CLIENT_ID_STORAGE_KEY);
      window.localStorage.removeItem('google_client_id');
    }
  } catch {
    // Sandbox or restricted iframe environment fallback
  }
}

export function getEnvGoogleClientId(): string {
  try {
    if (typeof import.meta !== 'undefined' && (import.meta as any).env?.VITE_GOOGLE_CLIENT_ID) {
      const val = String((import.meta as any).env.VITE_GOOGLE_CLIENT_ID);
      if (isValidClientIdString(val)) {
        return val.trim().replace(/^["']|["']$/g, '').trim();
      }
    }
  } catch {
    // ignore
  }
  try {
    if (typeof process !== 'undefined' && process.env?.VITE_GOOGLE_CLIENT_ID) {
      const val = String(process.env.VITE_GOOGLE_CLIENT_ID);
      if (isValidClientIdString(val)) {
        return val.trim().replace(/^["']|["']$/g, '').trim();
      }
    }
  } catch {
    // ignore
  }
  return '';
}

export function getGoogleClientId(): string {
  const stored = getStoredGoogleClientId();
  if (stored) return stored;
  return getEnvGoogleClientId();
}

export function hasGoogleClientId(): boolean {
  return Boolean(getGoogleClientId());
}

export function isGoogleIdentityLoaded(): boolean {
  return (
    typeof window !== 'undefined' &&
    Boolean(window.google?.accounts?.oauth2?.initTokenClient)
  );
}

export async function loadGoogleIdentityScript(timeoutMs: number = 3000): Promise<void> {
  if (isGoogleIdentityLoaded()) return;
  if (typeof window === 'undefined' || typeof document === 'undefined') return;

  return new Promise((resolve, reject) => {
    let timeoutId: any = null;
    const cleanup = () => {
      if (timeoutId) {
        clearTimeout(timeoutId);
        timeoutId = null;
      }
    };

    const existing = document.querySelector('script[src="https://accounts.google.com/gsi/client"]');
    if (existing) {
      if (isGoogleIdentityLoaded()) {
        resolve();
        return;
      }
      const handleLoad = () => {
        cleanup();
        resolve();
      };
      const handleError = () => {
        cleanup();
        try {
          existing.remove();
        } catch {
          // ignore
        }
        reject(
          new Error(
            'Failed to load Google Identity Services SDK (network offline or blocked). Please check your internet connection or use the Manual Access Token fallback.'
          )
        );
      };
      existing.addEventListener('load', handleLoad, { once: true });
      existing.addEventListener('error', handleError, { once: true });
      if (timeoutMs > 0) {
        timeoutId = setTimeout(() => {
          existing.removeEventListener('load', handleLoad);
          existing.removeEventListener('error', handleError);
          try {
            existing.remove();
          } catch {
            // ignore
          }
          reject(
            new Error(
              'Failed to load Google Identity Services SDK (timeout). Please check your internet connection or use the Manual Access Token fallback.'
            )
          );
        }, timeoutMs);
      }
      return;
    }

    const script = document.createElement('script');
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.defer = true;

    const handleScriptLoad = () => {
      cleanup();
      resolve();
    };

    const handleScriptError = () => {
      cleanup();
      try {
        script.remove();
      } catch {
        // ignore
      }
      reject(
        new Error(
          'Failed to load Google Identity Services SDK (network offline or blocked). Please check your internet connection or use the Manual Access Token fallback.'
        )
      );
    };

    script.addEventListener('load', handleScriptLoad, { once: true });
    script.addEventListener('error', handleScriptError, { once: true });

    if (timeoutMs > 0) {
      timeoutId = setTimeout(() => {
        script.removeEventListener('load', handleScriptLoad);
        script.removeEventListener('error', handleScriptError);
        try {
          script.remove();
        } catch {
          // ignore
        }
        reject(
          new Error(
            'Failed to load Google Identity Services SDK (timeout). Please check your internet connection or use the Manual Access Token fallback.'
          )
        );
      }, timeoutMs);
    }

    try {
      document.head.appendChild(script);
    } catch (e) {
      cleanup();
      reject(e);
    }
  });
}

export function requestGoogleAccessToken(options: GoogleAuthOptions): Promise<string> {
  return new Promise(async (resolve, reject) => {
    try {
      if (!isGoogleIdentityLoaded()) {
        await loadGoogleIdentityScript();
      }

      if (!window.google?.accounts?.oauth2?.initTokenClient) {
        throw new Error(
          'Google Identity Services SDK is not available in the current environment. Please use the Manual Access Token fallback.'
        );
      }

      const rawClientId = options.clientId || getGoogleClientId();
      const clientId = rawClientId ? rawClientId.trim().replace(/^["']|["']$/g, '').trim() : '';
      if (!isValidClientIdString(clientId)) {
        throw new Error(
          'Google OAuth Client ID is not configured. Please configure your Client ID in settings or provide a manual OAuth access token.'
        );
      }

      const tokenClient = window.google.accounts.oauth2.initTokenClient({
        client_id: clientId,
        scope: options.scope,
        prompt: options.prompt || '',
        hint: options.hint || '',
        callback: (response: GisTokenResponse) => {
          if (response?.access_token) {
            resolve(response.access_token);
          } else if (response?.error) {
            const errorMsg = response.error_description || response.error;
            if (
              response.error === 'invalid_client' ||
              (typeof errorMsg === 'string' && errorMsg.toLowerCase().includes('invalid_client'))
            ) {
              const currentOrigin =
                typeof window !== 'undefined' && window.location?.origin && window.location.origin !== 'null'
                  ? window.location.origin
                  : 'http://localhost:5173';
              reject(
                new Error(
                  `Google OAuth authorization failed: invalid_client. Your Google OAuth Client ID may be invalid or missing authorized JavaScript origins (${currentOrigin}).`
                )
              );
            } else {
              reject(new Error(`Google authorization error: ${errorMsg}`));
            }
          } else {
            reject(new Error('Google authorization was cancelled or returned no token.'));
          }
        },
        error_callback: (err?: any) => {
          const errType = (err && typeof err === 'object' && err.type) ? String(err.type) : (typeof err === 'string' ? err : '');
          const errMsg = (err && typeof err === 'object' && err.message) ? String(err.message) : '';
          const fullErr = `${errType} ${errMsg}`.trim();
          if (
            errType === 'invalid_client' ||
            fullErr.toLowerCase().includes('invalid_client')
          ) {
            const currentOrigin =
              typeof window !== 'undefined' && window.location?.origin && window.location.origin !== 'null'
                ? window.location.origin
                : 'http://localhost:5173';
            reject(
              new Error(
                `Google OAuth authorization failed: invalid_client. Your Google OAuth Client ID may be invalid or missing authorized JavaScript origins (${currentOrigin}).`
              )
            );
          } else if (
            errType === 'popup_blocked_by_browser' ||
            fullErr.toLowerCase().includes('popup_blocked') ||
            fullErr.toLowerCase().includes('popup blocked')
          ) {
            reject(
              new Error(
                'Google OAuth authorization popup was blocked by the browser. Please allow popups or use the Manual Access Token fallback.'
              )
            );
          } else if (
            errType === 'popup_closed' ||
            fullErr.toLowerCase().includes('popup_closed') ||
            fullErr.toLowerCase().includes('closed by user')
          ) {
            reject(
              new Error(
                'Google OAuth popup was closed before authorization completed.'
              )
            );
          } else {
            reject(
              new Error(
                `Google OAuth error: ${errMsg || errType || 'Popup closed or blocked'}`
              )
            );
          }
        },
      });

      tokenClient.requestAccessToken({ prompt: options.prompt, hint: options.hint });
    } catch (err: any) {
      reject(err);
    }
  });
}
