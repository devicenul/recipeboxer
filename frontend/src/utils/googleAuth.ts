/**
 * Shared Google Identity Services (GIS) OAuth 2.0 Token Client Helper
 */

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

export function getGoogleClientId(): string {
  const envId =
    typeof import.meta !== 'undefined'
      ? (import.meta as any).env?.VITE_GOOGLE_CLIENT_ID
      : '';
  return envId || 'recipeboxer-default-client-id';
}

export function isGoogleIdentityLoaded(): boolean {
  return (
    typeof window !== 'undefined' &&
    Boolean(window.google?.accounts?.oauth2?.initTokenClient)
  );
}

export async function loadGoogleIdentityScript(): Promise<void> {
  if (isGoogleIdentityLoaded()) return;
  if (typeof window === 'undefined' || typeof document === 'undefined') return;

  return new Promise((resolve, reject) => {
    const existing = document.querySelector('script[src="https://accounts.google.com/gsi/client"]');
    if (existing) {
      existing.addEventListener('load', () => resolve());
      existing.addEventListener('error', () =>
        reject(new Error('Failed to load Google Identity Services SDK'))
      );
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.defer = true;
    script.onload = () => resolve();
    script.onerror = () =>
      reject(new Error('Failed to load Google Identity Services SDK'));
    document.head.appendChild(script);
  });
}

export function requestGoogleAccessToken(options: GoogleAuthOptions): Promise<string> {
  return new Promise(async (resolve, reject) => {
    try {
      if (!isGoogleIdentityLoaded()) {
        await loadGoogleIdentityScript();
      }

      if (!window.google?.accounts?.oauth2?.initTokenClient) {
        throw new Error('Google Identity Services SDK is not available in the current environment.');
      }

      const clientId = options.clientId || getGoogleClientId();
      if (!clientId) {
        throw new Error('Google OAuth Client ID is not configured (VITE_GOOGLE_CLIENT_ID).');
      }

      const tokenClient = window.google.accounts.oauth2.initTokenClient({
        client_id: clientId,
        scope: options.scope,
        prompt: options.prompt || '',
        hint: options.hint || '',
        callback: (response: GisTokenResponse) => {
          if (response.access_token) {
            resolve(response.access_token);
          } else if (response.error) {
            const errorMsg = response.error_description || response.error;
            reject(new Error(`Google authorization error: ${errorMsg}`));
          } else {
            reject(new Error('Google authorization was cancelled or returned no token.'));
          }
        },
        error_callback: (err) => {
          reject(new Error(`Google OAuth error: ${err.message || err.type || 'Popup closed or blocked'}`));
        },
      });

      tokenClient.requestAccessToken({ prompt: options.prompt, hint: options.hint });
    } catch (err: any) {
      reject(err);
    }
  });
}
