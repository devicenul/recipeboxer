import React, { useState, useEffect } from 'react';
import { Settings as SettingsIcon, Save, RefreshCw, CheckCircle2, AlertCircle } from 'lucide-react';
import { useSettings } from '../context/SettingsContext';
import { QualityProfileSelector, QualityProfile } from '../components/settings/QualityProfileSelector';
import { SpendCapsSection } from '../components/settings/SpendCapsSection';
import { ApiKeySection } from '../components/settings/ApiKeySection';
import { CostEstimateCalculator } from '../components/settings/CostEstimateCalculator';
import { SettingsUpdateRequest } from '../types/pipeline';

export const SettingsPage: React.FC = () => {
  const {
    settings,
    isLoading,
    error,
    refreshSettings,
    updateSettings,
    saveApiKey,
    removeApiKey,
    hasApiKey,
    keySource,
  } = useSettings();

  // Local draft state for form controls
  const [profile, setProfile] = useState<string>('balanced');
  const [spendCapUsd, setSpendCapUsd] = useState<number | null>(null);
  const [monthlyCapUsd, setMonthlyCapUsd] = useState<number | null>(null);
  const [rpmLimit, setRpmLimit] = useState<number>(60);
  const [extraEffort, setExtraEffort] = useState<boolean>(false);
  const [embeddingsEnabled, setEmbeddingsEnabled] = useState<boolean>(false);
  const [stageModels, setStageModels] = useState<Record<string, string>>({});

  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [saveMessage, setSaveMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  // Sync draft state when settings load
  useEffect(() => {
    if (settings) {
      setProfile(settings.quality_profile || 'balanced');
      setSpendCapUsd(settings.spend_cap_usd !== undefined ? settings.spend_cap_usd : null);
      setMonthlyCapUsd(settings.monthly_cap_usd !== undefined ? settings.monthly_cap_usd : null);
      setRpmLimit(settings.rpm_limit || 60);
      setExtraEffort(!!settings.extra_effort);
      setEmbeddingsEnabled(!!settings.embeddings_enabled);
      setStageModels(settings.stage_models || {});
    }
  }, [settings]);

  const handleProfileChange = async (newProfile: QualityProfile) => {
    setProfile(newProfile);
    try {
      await updateSettings({ quality_profile: newProfile });
      setSaveMessage({ text: `Switched to ${newProfile} profile`, type: 'success' });
    } catch {
      // Handled via context
    }
  };

  const handleSpendSectionChange = (updates: {
    spend_cap_usd?: number | null;
    monthly_cap_usd?: number | null;
    rpm_limit?: number;
    extra_effort?: boolean;
    embeddings_enabled?: boolean;
    stage_models?: Record<string, string>;
  }) => {
    if (updates.spend_cap_usd !== undefined) setSpendCapUsd(updates.spend_cap_usd);
    if (updates.monthly_cap_usd !== undefined) setMonthlyCapUsd(updates.monthly_cap_usd);
    if (updates.rpm_limit !== undefined) setRpmLimit(updates.rpm_limit);
    if (updates.extra_effort !== undefined) setExtraEffort(updates.extra_effort);
    if (updates.embeddings_enabled !== undefined) setEmbeddingsEnabled(updates.embeddings_enabled);
    if (updates.stage_models !== undefined) setStageModels(updates.stage_models);
  };

  const handleSaveAllPreferences = async () => {
    setIsSaving(true);
    setSaveMessage(null);
    try {
      const payload: SettingsUpdateRequest = {
        quality_profile: profile,
        spend_cap_usd: spendCapUsd,
        monthly_cap_usd: monthlyCapUsd,
        rpm_limit: rpmLimit,
        extra_effort: extraEffort,
        embeddings_enabled: embeddingsEnabled,
        stage_models: stageModels,
      };
      await updateSettings(payload);
      setSaveMessage({ text: 'All settings and spend caps saved successfully', type: 'success' });
    } catch (err: any) {
      setSaveMessage({ text: err?.message || 'Failed to save settings', type: 'error' });
    } finally {
      setIsSaving(false);
    }
  };

  const handleSaveKey = async (key: string) => {
    await saveApiKey(key);
  };

  const handleRemoveKey = async () => {
    await removeApiKey();
  };

  return (
    <div className="max-w-5xl mx-auto space-y-8 pb-16">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-4 border-b border-slate-200 dark:border-slate-800">
        <div>
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-lg bg-blue-100 dark:bg-blue-950 text-blue-700 dark:text-blue-300">
              <SettingsIcon className="w-5 h-5" />
            </div>
            <h1 className="text-xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
              Settings & Usage Controls
            </h1>
          </div>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
            Configure Gemini AI quality profiles, spending safety caps, requests per minute limits, and API credentials.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => refreshSettings()}
            disabled={isLoading}
            className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-slate-700 dark:text-slate-300 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors cursor-pointer disabled:opacity-50"
            title="Refresh settings from server"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>

          <button
            type="button"
            onClick={handleSaveAllPreferences}
            disabled={isSaving || isLoading}
            className="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600 rounded-lg shadow-xs transition-colors cursor-pointer disabled:opacity-50"
            data-testid="save-all-settings-button"
          >
            <Save className="w-3.5 h-3.5" />
            <span>{isSaving ? 'Saving...' : 'Save Settings'}</span>
          </button>
        </div>
      </div>

      {saveMessage && (
        <div
          className={`p-3 rounded-lg text-xs flex items-center gap-2 ${
            saveMessage.type === 'success'
              ? 'bg-emerald-50 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800'
              : 'bg-rose-50 text-rose-800 dark:bg-rose-950/40 dark:text-rose-300 border border-rose-200 dark:border-rose-800'
          }`}
        >
          {saveMessage.type === 'success' ? (
            <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-600" />
          ) : (
            <AlertCircle className="w-4 h-4 shrink-0 text-rose-600" />
          )}
          <span>{saveMessage.text}</span>
        </div>
      )}

      {error && (
        <div className="p-3 rounded-lg text-xs bg-rose-50 text-rose-800 dark:bg-rose-950/40 dark:text-rose-300 border border-rose-200 dark:border-rose-800 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0 text-rose-600" />
          <span>{error}</span>
        </div>
      )}

      {/* 1. Quality Profile Selector */}
      <section className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-xs">
        <QualityProfileSelector
          value={profile}
          onChange={handleProfileChange}
          hasApiKey={hasApiKey}
        />
      </section>

      {/* 2. Spend Caps & Rate Limits */}
      <section className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-xs">
        <SpendCapsSection
          spendCapUsd={spendCapUsd}
          monthlyCapUsd={monthlyCapUsd}
          rpmLimit={rpmLimit}
          extraEffort={extraEffort}
          embeddingsEnabled={embeddingsEnabled}
          stageModels={stageModels}
          onChange={handleSpendSectionChange}
        />
      </section>

      {/* 3. Pre-Run Cost Estimate */}
      <section className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-xs">
        <CostEstimateCalculator currentProfile={profile} />
      </section>

      {/* 4. API Key & Security */}
      <section className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-xs">
        <ApiKeySection
          hasApiKey={hasApiKey}
          keySource={keySource}
          onSaveKey={handleSaveKey}
          onRemoveKey={handleRemoveKey}
        />
      </section>
    </div>
  );
};
