import React, { useState, useEffect } from 'react';
import { Key, CheckCircle, XCircle, Trash2, ExternalLink, ShieldAlert, Info, Lock } from 'lucide-react';
import { PrivacyNoticeResponse } from '../../types/pipeline';
import { api } from '../../utils/api';

export interface ApiKeySectionProps {
  hasApiKey: boolean;
  keySource: string;
  onSaveKey: (key: string) => Promise<void>;
  onRemoveKey: () => Promise<void>;
}

export const ApiKeySection: React.FC<ApiKeySectionProps> = ({
  hasApiKey,
  keySource,
  onSaveKey,
  onRemoveKey,
}) => {
  const [inputKey, setInputKey] = useState<string>('');
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [isRemoving, setIsRemoving] = useState<boolean>(false);
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  const [privacyNotice, setPrivacyNotice] = useState<PrivacyNoticeResponse | null>(null);

  useEffect(() => {
    let isMounted = true;
    api
      .getPrivacyNotice()
      .then((data) => {
        if (isMounted) setPrivacyNotice(data);
      })
      .catch(() => {
        // Fallback default notice if endpoint is unreachable
        if (isMounted) {
          setPrivacyNotice({
            notice:
              "When using Google's unpaid / free-tier Gemini API, prompt data and card images may be reviewed by human reviewers and utilized by Google to train and improve products. Paid Gemini API usage generally does not permit data usage for model training. Because personal and family recipe cards frequently contain private handwriting, family names, and personal margin notes, please exercise caution and review Google's terms before proceeding.",
            terms_url: 'https://ai.google.dev/gemini-api/terms',
            privacy_policy_url: 'https://policies.google.com/privacy',
            paid_vs_free_summary:
              'Free tier: Data may be retained and used for Google product improvement. Paid tier: Data is not used to train Google models and remains strictly private.',
          });
        }
      });
    return () => {
      isMounted = false;
    };
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    const clean = inputKey.trim();
    if (!clean) {
      setMessage({ text: 'Please enter a valid Gemini API key', type: 'error' });
      return;
    }
    setIsSaving(true);
    setMessage(null);
    try {
      await onSaveKey(clean);
      setInputKey('');
      setMessage({ text: 'Gemini API key saved securely', type: 'success' });
    } catch (err: any) {
      setMessage({ text: err?.message || 'Failed to save API key', type: 'error' });
    } finally {
      setIsSaving(false);
    }
  };

  const handleRemove = async () => {
    if (!window.confirm('Are you sure you want to remove the configured Gemini API key?')) {
      return;
    }
    setIsRemoving(true);
    setMessage(null);
    try {
      await onRemoveKey();
      setMessage({ text: 'Gemini API key removed', type: 'success' });
    } catch (err: any) {
      setMessage({ text: err?.message || 'Failed to remove API key', type: 'error' });
    } finally {
      setIsRemoving(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200 flex items-center gap-2">
          <Key className="w-4 h-4 text-blue-600 dark:text-blue-400" />
          Google Gemini API Key
        </h3>
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
          Configure credentials for multimodal handwriting transcription and recipe analysis.
        </p>
      </div>

      {/* Key Status & Storage Source */}
      <div className="p-3.5 bg-slate-50 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 rounded-xl flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2.5">
          {hasApiKey ? (
            <CheckCircle className="w-5 h-5 text-emerald-500 shrink-0" />
          ) : (
            <XCircle className="w-5 h-5 text-amber-500 shrink-0" />
          )}
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-slate-900 dark:text-slate-100">
                {hasApiKey ? 'API Key Configured' : 'No API Key Configured'}
              </span>
              <span
                className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                  hasApiKey
                    ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300'
                    : 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300'
                }`}
              >
                {hasApiKey ? 'Ready' : 'Analysis Blocked'}
              </span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5 flex items-center gap-1">
              <Lock className="w-3 h-3 text-slate-400" />
              <span>Storage Source:</span>
              <span className="font-mono text-slate-700 dark:text-slate-300 font-semibold">
                {keySource}
              </span>
            </p>
          </div>
        </div>

        {hasApiKey && (
          <button
            type="button"
            onClick={handleRemove}
            disabled={isRemoving}
            className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/40 border border-rose-200 dark:border-rose-900/50 rounded-lg transition-colors cursor-pointer disabled:opacity-50"
            data-testid="remove-api-key-button"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>{isRemoving ? 'Removing...' : 'Remove Key'}</span>
          </button>
        )}
      </div>

      {/* Set API Key Form */}
      <form onSubmit={handleSave} className="space-y-3">
        <div>
          <label
            htmlFor="gemini-api-key-input"
            className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1"
          >
            {hasApiKey ? 'Update API Key' : 'Enter Gemini API Key'}
          </label>
          <div className="flex items-center gap-2">
            <input
              id="gemini-api-key-input"
              type="password"
              placeholder="AIzaSy..."
              value={inputKey}
              onChange={(e) => setInputKey(e.target.value)}
              className="flex-1 px-3 py-1.5 text-xs font-mono rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-blue-500"
              data-testid="api-key-input"
            />
            <button
              type="submit"
              disabled={isSaving || !inputKey.trim()}
              className="px-4 py-1.5 text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600 rounded-lg transition-colors cursor-pointer disabled:opacity-50"
              data-testid="save-api-key-button"
            >
              {isSaving ? 'Saving...' : 'Save Key'}
            </button>
          </div>
          <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
            Keys are encrypted in OS keyring (fallback to local permissions 0600 file). The key is never displayed or returned over the API.
          </p>
        </div>

        {message && (
          <div
            className={`p-2.5 rounded-lg text-xs flex items-center gap-2 ${
              message.type === 'success'
                ? 'bg-emerald-50 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800'
                : 'bg-rose-50 text-rose-800 dark:bg-rose-950/40 dark:text-rose-300 border border-rose-200 dark:border-rose-800'
            }`}
          >
            {message.type === 'success' ? (
              <CheckCircle className="w-3.5 h-3.5 shrink-0" />
            ) : (
              <XCircle className="w-3.5 h-3.5 shrink-0" />
            )}
            <span>{message.text}</span>
          </div>
        )}
      </form>

      {/* Free Tier vs Paid Explainer Card */}
      <div className="p-4 bg-blue-50/60 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-900/60 rounded-xl space-y-3">
        <div className="flex items-center gap-2 text-xs font-bold text-blue-900 dark:text-blue-200">
          <Info className="w-4 h-4 text-blue-600 dark:text-blue-400 shrink-0" />
          <span>Free Tier vs. Paid Key Comparison</span>
        </div>

        <div className="text-xs text-slate-700 dark:text-slate-300 space-y-2 leading-relaxed">
          <p>
            <strong>Free Tier:</strong> Free of charge, but limited to{' '}
            <span className="font-semibold text-slate-900 dark:text-slate-100">
              very low daily limits, may change; see Google&apos;s current limits
            </span>{' '}
            (roughly 20 requests/day per model observed). Furthermore, Pro models (such as
            gemini-3.1-pro) are unavailable on the free tier, meaning the Best quality profile cannot run.
          </p>

          <p>
            <strong>Privacy Consideration:</strong> Under Google&apos;s unpaid / free-tier API terms, prompt text and card images may be reviewed by human reviewers and utilized by Google to improve its AI models and products.
          </p>

          <p className="p-2.5 bg-white/70 dark:bg-slate-900/70 border border-blue-200 dark:border-blue-900/40 rounded-lg text-slate-800 dark:text-slate-200 font-medium">
            💡 <strong>Recommendation:</strong> For personal and family recipe cards containing family names, handwriting, and cherished notes, we strongly recommend configuring a{' '}
            <span className="text-blue-600 dark:text-blue-400 font-bold">paid Google Cloud API key</span>{' '}
            with a modest spend cap (e.g. $2.00–$5.00). Paid usage prevents your data from being used for model training and unlocks full Gemini Pro accuracy.
          </p>
        </div>
      </div>

      {/* Official Privacy Notice & External Terms Links */}
      {privacyNotice && (
        <div className="p-4 bg-slate-50 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 rounded-xl text-xs space-y-2.5">
          <div className="flex items-center gap-1.5 font-semibold text-slate-800 dark:text-slate-200">
            <ShieldAlert className="w-4 h-4 text-slate-500" />
            <span>Official Privacy Guidance</span>
          </div>

          <p className="text-slate-600 dark:text-slate-400 leading-relaxed text-[11px]">
            {privacyNotice.notice}
          </p>

          <div className="flex flex-wrap items-center gap-4 pt-1">
            <a
              href={privacyNotice.terms_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-[11px] font-medium text-blue-600 dark:text-blue-400 hover:underline"
            >
              <span>Google Gemini API Terms</span>
              <ExternalLink className="w-3 h-3" />
            </a>

            <a
              href={privacyNotice.privacy_policy_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-[11px] font-medium text-blue-600 dark:text-blue-400 hover:underline"
            >
              <span>Google Privacy Policy</span>
              <ExternalLink className="w-3 h-3" />
            </a>
          </div>
        </div>
      )}
    </div>
  );
};
