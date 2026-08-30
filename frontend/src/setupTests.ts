import '@testing-library/jest-dom';
import { vi } from 'vitest';

// Polyfill localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] || null,
    setItem: (key: string, value: string) => {
      store[key] = String(value);
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();

Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
  writable: true,
});

// Polyfill window.matchMedia if not available in jsdom
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// Polyfill URL.createObjectURL / revokeObjectURL for export tests
if (!global.URL.createObjectURL) {
  global.URL.createObjectURL = () => 'blob:mock-url';
}
if (!global.URL.revokeObjectURL) {
  global.URL.revokeObjectURL = () => {};
}

// Polyfill Google Identity Services (GIS)
let mockGisResponse: {
  access_token?: string;
  error?: string;
  error_description?: string;
  error_uri?: string;
} = {
  access_token: 'ya29.mock_gis_test_token',
};

let mockTokenClientCallback: ((resp: any) => void) | null = null;
let mockTokenClientErrorCallback: ((err: any) => void) | null = null;

const mockTokenClient = {
  requestAccessToken: vi.fn().mockImplementation((_overrideConfig?: any) => {
    if (mockTokenClientCallback) {
      mockTokenClientCallback(mockGisResponse);
    }
  }),
};

export const setMockGisResponse = (resp: typeof mockGisResponse) => {
  mockGisResponse = resp;
};

export const resetMockGisResponse = () => {
  mockGisResponse = { access_token: 'ya29.mock_gis_test_token' };
};

export const mockInitTokenClient = vi.fn().mockImplementation((config: any) => {
  mockTokenClientCallback = config.callback;
  mockTokenClientErrorCallback = config.error_callback || null;
  return mockTokenClient;
});

Object.defineProperty(window, 'google', {
  writable: true,
  value: {
    accounts: {
      oauth2: {
        initTokenClient: mockInitTokenClient,
        hasGrantedAllScopes: vi.fn(() => true),
        hasGrantedAnyScope: vi.fn(() => true),
        revoke: vi.fn((_token, done) => done && done()),
      },
    },
  },
});
