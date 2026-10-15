import React, { useState, useEffect, useRef } from 'react';
import { Play, AlertCircle, Archive, Database, Folder, Upload, CheckCircle2, X } from 'lucide-react';
import { Button } from '../common/Button';
import { ConfigPanel } from './ConfigPanel';
import { SourceSelector, IngestionSourceType } from './SourceSelector';
import { PipelineStartRequest, IngestionMode, SessionSummary } from '../../types/pipeline';
import { SourceContainer } from '../../types/sources';
import { api } from '../../utils/api';

export interface SessionLauncherProps {
  onSessionStarted: (sessionId: string) => void;
  isStarting?: boolean;
}

export const SessionLauncher: React.FC<SessionLauncherProps> = ({
  onSessionStarted,
  isStarting = false,
}) => {
  const [sourceType, setSourceType] = useState<IngestionSourceType>('local');
  const [localMethod, setLocalMethod] = useState<'folder' | 'files'>('folder');
  const [folderPath, setFolderPath] = useState<string>('');
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
  const [selectedContainer, setSelectedContainer] = useState<SourceContainer | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<string>('FOOD');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  // Baseline Archive & Incremental Ingestion state
  const [baselineMode, setBaselineMode] = useState<'none' | 'file' | 'session' | 'path'>('none');
  const [baselineFile, setBaselineFile] = useState<File | null>(null);
  const [baselineSessionId, setBaselineSessionId] = useState<string>('');
  const [baselinePath, setBaselinePath] = useState<string>('');
  const [availableSessions, setAvailableSessions] = useState<SessionSummary[]>([]);
  const baselineFileInputRef = useRef<HTMLInputElement>(null);

  const [config, setConfig] = useState<PipelineStartRequest>({
    mode: IngestionMode.PARALLEL,
    max_concurrency: 5,
    slow_batch_size: 4,
    slow_batch_interval_seconds: 2.0,
    model: 'gemini-3.8-flash',
    thinking_mode: false,
    thinking_budget_tokens: 1024,
  });

  useEffect(() => {
    let mounted = true;
    const loadSessions = async () => {
      try {
        const list = await api.listSessions();
        if (mounted && Array.isArray(list)) {
          setAvailableSessions(list.filter((s) => s.status === 'COMPLETED' || s.card_count > 0));
        }
      } catch {
        // Ignored if offline or server initializing
      }
    };
    loadSessions();
    return () => {
      mounted = false;
    };
  }, []);

  const handleLaunch = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const resolvedBaselinePath = baselineMode === 'path' && baselinePath.trim() ? baselinePath.trim() : undefined;
    const resolvedBaselineSid = baselineMode === 'session' && baselineSessionId.trim() ? baselineSessionId.trim() : undefined;

    if (sourceType === 'local') {
      if (localMethod === 'folder') {
        if (!folderPath.trim()) {
          setError('Please enter a valid scans folder path.');
          return;
        }

        setLoading(true);
        try {
          const payload: PipelineStartRequest = {
            ...config,
            scans_dir: folderPath.trim(),
            directory_path: folderPath.trim(),
            baseline_project_path: resolvedBaselinePath,
            baseline_session_id: resolvedBaselineSid,
          };
          const res = await api.startPipeline(payload);
          onSessionStarted(res.session_id);
        } catch (err: any) {
          setError(err.message || 'Failed to start pipeline with directory');
        } finally {
          setLoading(false);
        }
      } else {
        if (uploadedFiles.length === 0) {
          setError('Please select or drop at least one card image.');
          return;
        }

        setLoading(true);
        try {
          const formData = new FormData();
          uploadedFiles.forEach((file) => formData.append('files', file));
          formData.append('mode', typeof config.mode === 'string' ? config.mode : 'parallel');
          formData.append('max_concurrency', String(config.max_concurrency ?? 5));
          formData.append('slow_batch_size', String(config.slow_batch_size ?? 4));
          formData.append('slow_batch_interval_seconds', String(config.slow_batch_interval_seconds ?? 2.0));
          formData.append('model', config.model || 'gemini-3.8-flash');
          if (config.default_tags && config.default_tags.length > 0) {
            formData.append('default_tags', JSON.stringify(config.default_tags));
          }
          if (baselineMode === 'file' && baselineFile) {
            formData.append('baseline_archive', baselineFile);
          } else if (resolvedBaselinePath) {
            formData.append('baseline_project_path', resolvedBaselinePath);
          } else if (resolvedBaselineSid) {
            formData.append('baseline_session_id', resolvedBaselineSid);
          }
          formData.append('auto_start', 'true');

          const uploadRes = await api.uploadCards(formData);
          onSessionStarted(uploadRes.session_id);
        } catch (err: any) {
          setError(err.message || 'Failed to upload cards and start session');
        } finally {
          setLoading(false);
        }
      }
    } else if (sourceType === 'google_photos') {
      setLoading(true);
      try {
        const payload: PipelineStartRequest = {
          ...config,
          source_provider: 'google_photos',
          source_container_id: selectedContainer?.id,
          source_config: { category_filter: categoryFilter },
          baseline_project_path: resolvedBaselinePath,
          baseline_session_id: resolvedBaselineSid,
        };
        const res = await api.startPipeline(payload);
        onSessionStarted(res.session_id);
      } catch (err: any) {
        setError(err.message || 'Failed to launch ingestion from Google Photos');
      } finally {
        setLoading(false);
      }
    } else if (sourceType === 'google_drive') {
      setLoading(true);
      try {
        const payload: PipelineStartRequest = {
          ...config,
          source_provider: 'google_drive',
          source_container_id: selectedContainer?.id,
          baseline_project_path: resolvedBaselinePath,
          baseline_session_id: resolvedBaselineSid,
        };
        const res = await api.startPipeline(payload);
        onSessionStarted(res.session_id);
      } catch (err: any) {
        setError(err.message || 'Failed to launch ingestion from Google Drive');
      } finally {
        setLoading(false);
      }
    }
  };

  return (
    <form onSubmit={handleLaunch} className="flex flex-col gap-6" data-tour="ingestion-sources">
      {/* Pluggable Source Selector */}
      <SourceSelector
        sourceType={sourceType}
        onSourceTypeChange={setSourceType}
        localMethod={localMethod}
        onLocalMethodChange={setLocalMethod}
        folderPath={folderPath}
        onFolderPathChange={setFolderPath}
        uploadedFiles={uploadedFiles}
        onUploadedFilesChange={setUploadedFiles}
        selectedContainer={selectedContainer}
        onSelectContainer={setSelectedContainer}
        categoryFilter={categoryFilter}
        onCategoryFilterChange={setCategoryFilter}
        disabled={loading || isStarting}
      />

      {/* Baseline Project Archive / Incremental Ingestion Section */}
      <div className="p-5 bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-xs flex flex-col gap-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <Archive className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
            <div>
              <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">
                Baseline Project Archive (Incremental Ingestion)
              </h3>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Supply an existing <span className="font-mono text-indigo-600 dark:text-indigo-400">.recipeboxer</span> project to skip previously processed cards and merge existing recipes.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1.5 p-1 bg-slate-100 dark:bg-slate-800/80 rounded-lg text-xs font-medium">
            <button
              type="button"
              onClick={() => { setBaselineMode('none'); setBaselineFile(null); }}
              className={`px-2.5 py-1 rounded-md transition-all ${baselineMode === 'none' ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 shadow-xs font-semibold' : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'}`}
            >
              None
            </button>
            <button
              type="button"
              onClick={() => setBaselineMode('file')}
              className={`px-2.5 py-1 rounded-md transition-all flex items-center gap-1 ${baselineMode === 'file' ? 'bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-400 shadow-xs font-semibold' : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'}`}
            >
              <Upload className="w-3.5 h-3.5" />
              <span>Upload Archive</span>
            </button>
            <button
              type="button"
              onClick={() => setBaselineMode('session')}
              className={`px-2.5 py-1 rounded-md transition-all flex items-center gap-1 ${baselineMode === 'session' ? 'bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-400 shadow-xs font-semibold' : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'}`}
            >
              <Database className="w-3.5 h-3.5" />
              <span>Past Session</span>
            </button>
            <button
              type="button"
              onClick={() => setBaselineMode('path')}
              className={`px-2.5 py-1 rounded-md transition-all flex items-center gap-1 ${baselineMode === 'path' ? 'bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-400 shadow-xs font-semibold' : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'}`}
            >
              <Folder className="w-3.5 h-3.5" />
              <span>File Path</span>
            </button>
          </div>
        </div>

        {/* Baseline Mode: Upload Archive File */}
        {baselineMode === 'file' && (
          <div className="flex flex-col gap-2 pt-2 border-t border-slate-100 dark:border-slate-800">
            <input
              ref={baselineFileInputRef}
              type="file"
              accept=".recipeboxer,.zip"
              className="hidden"
              onChange={(e) => {
                if (e.target.files && e.target.files[0]) {
                  setBaselineFile(e.target.files[0]);
                }
              }}
            />
            {baselineFile ? (
              <div className="flex items-center justify-between p-3 bg-indigo-50 dark:bg-indigo-950/40 border border-indigo-200 dark:border-indigo-800 rounded-lg">
                <div className="flex items-center gap-2.5">
                  <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
                  <div>
                    <span className="text-xs font-semibold text-slate-800 dark:text-slate-200">
                      {baselineFile.name}
                    </span>
                    <span className="text-[11px] text-slate-500 dark:text-slate-400 ml-2">
                      ({(baselineFile.size / 1024).toFixed(1)} KB)
                    </span>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setBaselineFile(null)}
                  className="p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 rounded-md hover:bg-slate-200/50 dark:hover:bg-slate-800"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            ) : (
              <div
                onClick={() => baselineFileInputRef.current?.click()}
                className="border-2 border-dashed border-slate-300 dark:border-slate-700 hover:border-indigo-500 dark:hover:border-indigo-400 rounded-xl p-6 text-center cursor-pointer transition-all bg-slate-50/50 dark:bg-slate-800/30 flex flex-col items-center justify-center gap-2"
              >
                <Archive className="w-7 h-7 text-indigo-500 dark:text-indigo-400" />
                <span className="text-xs font-semibold text-slate-700 dark:text-slate-300">
                  Click to select or drop a baseline <span className="text-indigo-600 dark:text-indigo-400">.recipeboxer</span> project archive
                </span>
                <span className="text-[11px] text-slate-400">
                  Cards matching existing SHA-256 digests will be skipped automatically
                </span>
              </div>
            )}
          </div>
        )}

        {/* Baseline Mode: Select Past Session */}
        {baselineMode === 'session' && (
          <div className="flex flex-col gap-2 pt-2 border-t border-slate-100 dark:border-slate-800">
            <label htmlFor="baseline-session-select" className="text-xs font-medium text-slate-700 dark:text-slate-300">
              Select Prior Session as Baseline:
            </label>
            <select
              id="baseline-session-select"
              aria-label="Select Prior Session as Baseline"
              value={baselineSessionId}
              onChange={(e) => setBaselineSessionId(e.target.value)}
              className="w-full text-xs bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-2.5 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-indigo-500 focus:outline-hidden"
            >
              <option value="">-- Choose a past session --</option>
              {availableSessions.map((sess) => (
                <option key={sess.session_id} value={sess.session_id}>
                  {sess.session_id} ({sess.card_count} cards, {sess.recipe_count} recipes) — {sess.created_at ? new Date(sess.created_at).toLocaleDateString() : 'Active'}
                </option>
              ))}
            </select>
            {availableSessions.length === 0 && (
              <p className="text-[11px] text-slate-400 italic">No past completed sessions found in local store.</p>
            )}
          </div>
        )}

        {/* Baseline Mode: Filesystem Path */}
        {baselineMode === 'path' && (
          <div className="flex flex-col gap-2 pt-2 border-t border-slate-100 dark:border-slate-800">
            <label className="text-xs font-medium text-slate-700 dark:text-slate-300">
              Filesystem Path to Baseline <span className="font-mono text-indigo-600 dark:text-indigo-400">.recipeboxer</span> File:
            </label>
            <input
              type="text"
              value={baselinePath}
              onChange={(e) => setBaselinePath(e.target.value)}
              placeholder="e.g. /path/to/archive.recipeboxer or C:\scans\archive.recipeboxer"
              className="w-full text-xs font-mono bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-2.5 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-indigo-500 focus:outline-hidden"
            />
          </div>
        )}
      </div>

      {/* Pipeline Ingestion Throttling & Reasoning Config */}
      <ConfigPanel
        config={config}
        onChange={setConfig}
        disabled={loading || isStarting}
      />

      {/* Error Alert */}
      {error && (
        <div className="flex items-start gap-2.5 p-3.5 bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 rounded-xl text-xs text-rose-700 dark:text-rose-300">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {/* Launch Button */}
      <div className="flex justify-end">
        <Button
          type="submit"
          variant="primary"
          size="lg"
          isLoading={loading || isStarting}
          leftIcon={<Play className="w-5 h-5 fill-current" />}
        >
          Launch Digitization Pipeline
        </Button>
      </div>
    </form>
  );
};
