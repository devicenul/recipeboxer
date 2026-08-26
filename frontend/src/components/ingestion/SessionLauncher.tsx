import React, { useState } from 'react';
import { Play, AlertCircle } from 'lucide-react';
import { Button } from '../common/Button';
import { ConfigPanel } from './ConfigPanel';
import { SourceSelector, IngestionSourceType } from './SourceSelector';
import { PipelineStartRequest, IngestionMode } from '../../types/pipeline';
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
  const [bucketName, setBucketName] = useState<string>('');
  const [prefix, setPrefix] = useState<string>('');
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
          if (config.default_tags && config.default_tags.length > 0) {
            formData.append('default_tags', JSON.stringify(config.default_tags));
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
        };
        const res = await api.startPipeline(payload);
        onSessionStarted(res.session_id);
      } catch (err: any) {
        setError(err.message || 'Failed to launch ingestion from Google Drive');
      } finally {
        setLoading(false);
      }
    } else if (sourceType === 's3' || sourceType === 'gcs') {
      setLoading(true);
      try {
        const payload: PipelineStartRequest = {
          ...config,
          source_provider: sourceType,
          source_container_id: prefix.trim() || undefined,
          source_config: {
            bucket_name: bucketName.trim() || undefined,
            prefix: prefix.trim() || undefined,
          },
        };
        const res = await api.startPipeline(payload);
        onSessionStarted(res.session_id);
      } catch (err: any) {
        setError(err.message || `Failed to launch ingestion from ${sourceType.toUpperCase()}`);
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
        bucketName={bucketName}
        onBucketNameChange={setBucketName}
        prefix={prefix}
        onPrefixChange={setPrefix}
        disabled={loading || isStarting}
      />

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
