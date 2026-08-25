import React, { useState } from 'react';
import { FolderOpen, Play, Upload, AlertCircle } from 'lucide-react';
import { Button } from '../common/Button';
import { Dropzone } from '../common/Dropzone';
import { ConfigPanel } from './ConfigPanel';
import { PipelineStartRequest, IngestionMode } from '../../types/pipeline';
import { api } from '../../utils/api';

export interface SessionLauncherProps {
  onSessionStarted: (sessionId: string) => void;
  isStarting?: boolean;
}

export const SessionLauncher: React.FC<SessionLauncherProps> = ({
  onSessionStarted,
  isStarting = false,
}) => {
  const [inputMethod, setInputMethod] = useState<'folder' | 'files'>('folder');
  const [folderPath, setFolderPath] = useState<string>('');
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  const [config, setConfig] = useState<PipelineStartRequest>({
    mode: IngestionMode.PARALLEL,
    max_concurrency: 5,
    slow_batch_size: 4,
    slow_batch_interval_seconds: 2.0,
    model: 'gemini-3.7-flash',
    thinking_mode: false,
    thinking_budget_tokens: 1024,
  });

  const handleLaunch = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (inputMethod === 'folder') {
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
        formData.append('model', config.model || 'gemini-3.7-flash');
        formData.append('auto_start', 'true');

        const uploadRes = await api.uploadCards(formData);
        onSessionStarted(uploadRes.session_id);
      } catch (err: any) {
        setError(err.message || 'Failed to upload cards and start session');
      } finally {
        setLoading(false);
      }
    }
  };

  return (
    <form onSubmit={handleLaunch} className="flex flex-col gap-6">
      {/* Method Switcher */}
      <div className="flex border-b border-slate-200 dark:border-slate-800">
        <button
          type="button"
          onClick={() => {
            setInputMethod('folder');
            setError(null);
          }}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-semibold border-b-2 transition-all cursor-pointer ${
            inputMethod === 'folder'
              ? 'border-blue-600 text-blue-600 dark:text-blue-400'
              : 'border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
          }`}
        >
          <FolderOpen className="w-4 h-4" />
          Scans Folder Path
        </button>
        <button
          type="button"
          onClick={() => {
            setInputMethod('files');
            setError(null);
          }}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-semibold border-b-2 transition-all cursor-pointer ${
            inputMethod === 'files'
              ? 'border-blue-600 text-blue-600 dark:text-blue-400'
              : 'border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
          }`}
        >
          <Upload className="w-4 h-4" />
          Direct File Upload
        </button>
      </div>

      {/* Input Selection Area */}
      {inputMethod === 'folder' ? (
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 shadow-xs flex flex-col gap-3">
          <label className="text-sm font-semibold text-slate-800 dark:text-slate-200">
            Local Scans Directory Path
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="/path/to/recipe/scans"
              value={folderPath}
              onChange={(e) => setFolderPath(e.target.value)}
              disabled={loading || isStarting}
              className="flex-1 px-3.5 py-2 text-sm bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono"
            />
          </div>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Path to a local folder containing high-resolution scanned recipe card images (.jpg, .png, .webp, .tiff).
          </p>
        </div>
      ) : (
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 shadow-xs">
          <Dropzone
            files={uploadedFiles}
            onFilesChange={setUploadedFiles}
            disabled={loading || isStarting}
          />
        </div>
      )}

      {/* Config Panel */}
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
