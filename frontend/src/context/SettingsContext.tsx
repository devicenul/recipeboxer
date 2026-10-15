import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import {
  SettingsResponse,
  SettingsUpdateRequest,
  ApiKeyResponse,
  ApiKeyDeleteResponse,
} from '../types/pipeline';
import { api } from '../utils/api';

export interface SettingsContextType {
  settings: SettingsResponse | null;
  isLoading: boolean;
  error: string | null;
  refreshSettings: () => Promise<void>;
  updateSettings: (payload: SettingsUpdateRequest) => Promise<SettingsResponse>;
  saveApiKey: (apiKey: string) => Promise<ApiKeyResponse>;
  removeApiKey: () => Promise<ApiKeyDeleteResponse>;
  hasApiKey: boolean;
  keySource: string;
}

const DEFAULT_SETTINGS: SettingsResponse = {
  quality_profile: 'balanced',
  stage_models: {},
  spend_cap_usd: null,
  monthly_cap_usd: null,
  rpm_limit: 60,
  extra_effort: false,
  embeddings_enabled: false,
  has_api_key: false,
  key_source: 'none',
};

const DEFAULT_SETTINGS_CONTEXT: SettingsContextType = {
  settings: DEFAULT_SETTINGS,
  isLoading: false,
  error: null,
  refreshSettings: async () => {},
  updateSettings: async (payload) => ({
    ...DEFAULT_SETTINGS,
    ...payload,
  }),
  saveApiKey: async () => ({
    success: true,
    has_api_key: true,
    key_source: 'keyring',
    message: 'Saved',
  }),
  removeApiKey: async () => ({
    success: true,
    has_api_key: false,
    key_source: 'none',
    message: 'Removed',
  }),
  hasApiKey: false,
  keySource: 'none',
};

const SettingsContext = createContext<SettingsContextType | undefined>(undefined);

export const SettingsProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const refreshSettings = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      if (typeof api?.getSettings === 'function') {
        const data = await api.getSettings();
        setSettings(data);
      } else {
        setSettings(DEFAULT_SETTINGS);
      }
    } catch (err: any) {
      setError(err?.message || 'Failed to load settings');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshSettings();
  }, [refreshSettings]);

  const updateSettings = useCallback(async (payload: SettingsUpdateRequest): Promise<SettingsResponse> => {
    setError(null);
    try {
      if (typeof api?.updateSettings === 'function') {
        const updated = await api.updateSettings(payload);
        setSettings(updated);
        return updated;
      }
      const merged = { ...(settings || DEFAULT_SETTINGS), ...payload };
      setSettings(merged);
      return merged;
    } catch (err: any) {
      setError(err?.message || 'Failed to update settings');
      throw err;
    }
  }, [settings]);

  const saveApiKey = useCallback(async (apiKey: string): Promise<ApiKeyResponse> => {
    setError(null);
    try {
      if (typeof api?.setApiKey === 'function') {
        const resp = await api.setApiKey(apiKey);
        setSettings((prev) => (prev ? { ...prev, has_api_key: true, key_source: resp.key_source } : null));
        await refreshSettings();
        return resp;
      }
      return { success: true, has_api_key: true, key_source: 'keyring', message: 'Saved' };
    } catch (err: any) {
      setError(err?.message || 'Failed to save API key');
      throw err;
    }
  }, [refreshSettings]);

  const removeApiKey = useCallback(async (): Promise<ApiKeyDeleteResponse> => {
    setError(null);
    try {
      if (typeof api?.deleteApiKey === 'function') {
        const resp = await api.deleteApiKey();
        setSettings((prev) => (prev ? { ...prev, has_api_key: resp.has_api_key, key_source: resp.key_source } : null));
        await refreshSettings();
        return resp;
      }
      return { success: true, has_api_key: false, key_source: 'none', message: 'Removed' };
    } catch (err: any) {
      setError(err?.message || 'Failed to remove API key');
      throw err;
    }
  }, [refreshSettings]);

  return (
    <SettingsContext.Provider
      value={{
        settings,
        isLoading,
        error,
        refreshSettings,
        updateSettings,
        saveApiKey,
        removeApiKey,
        hasApiKey: settings?.has_api_key ?? false,
        keySource: settings?.key_source ?? 'none',
      }}
    >
      {children}
    </SettingsContext.Provider>
  );
};

export function useSettings(): SettingsContextType {
  const context = useContext(SettingsContext);
  if (!context) {
    return DEFAULT_SETTINGS_CONTEXT;
  }
  return context;
}
