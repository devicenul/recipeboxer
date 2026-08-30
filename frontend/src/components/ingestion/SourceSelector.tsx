import React, { useEffect, useState, useRef } from 'react';
import {
  FolderOpen,
  Upload,
  Camera,
  Cloud,
  CheckCircle2,
  AlertCircle,
  Folder,
  Loader2,
} from 'lucide-react';
import { Dropzone } from '../common/Dropzone';
import { Button } from '../common/Button';
import { Badge } from '../common/Badge';
import { SourceConnectionStatus, SourceContainer } from '../../types/sources';
import { AlbumPickerModal } from './AlbumPickerModal';
import { api } from '../../utils/api';
import { getOsPathPlaceholder } from '../../utils/platform';
import { requestGoogleAccessToken, GOOGLE_SCOPES } from '../../utils/googleAuth';

export const isDirectoryPickerSupported = (): boolean =>
  typeof window !== 'undefined' && typeof (window as any).showDirectoryPicker === 'function';

export type IngestionSourceType = 'local' | 'google_photos' | 'google_drive' | 's3' | 'gcs';

export interface SourceSelectorProps {
  sourceType: IngestionSourceType;
  onSourceTypeChange: (source: IngestionSourceType) => void;
  localMethod: 'folder' | 'files';
  onLocalMethodChange: (method: 'folder' | 'files') => void;
  folderPath: string;
  onFolderPathChange: (path: string) => void;
  uploadedFiles: File[];
  onUploadedFilesChange: (files: File[]) => void;
  selectedContainer: SourceContainer | null;
  onSelectContainer: (container: SourceContainer | null) => void;
  categoryFilter: string;
  onCategoryFilterChange: (cat: string) => void;
  bucketName: string;
  onBucketNameChange: (b: string) => void;
  prefix: string;
  onPrefixChange: (p: string) => void;
  disabled?: boolean;
}

export const SourceSelector: React.FC<SourceSelectorProps> = ({
  sourceType,
  onSourceTypeChange,
  localMethod,
  onLocalMethodChange,
  folderPath,
  onFolderPathChange,
  uploadedFiles,
  onUploadedFilesChange,
  selectedContainer,
  onSelectContainer,
  categoryFilter,
  onCategoryFilterChange,
  bucketName,
  onBucketNameChange,
  prefix,
  onPrefixChange,
  disabled = false,
}) => {
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [providerStatuses, setProviderStatuses] = useState<Record<string, SourceConnectionStatus>>({});
  const [isConnecting, setIsConnecting] = useState<'google_photos' | 'google_drive' | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const handleBrowseDirectory = async () => {
    if (disabled) return;
    try {
      const res = await api.browseDirectory(folderPath);
      if (res && res.success) {
        if (res.canceled) {
          return;
        }
        if (res.path) {
          onFolderPathChange(res.path);
          return;
        }
      }
    } catch {
      // Fallback gracefully to browser directory picker if native backend browse fails/is unsupported
    }

    if (isDirectoryPickerSupported()) {
      try {
        const dirHandle = await (window as any).showDirectoryPicker({ mode: 'read' });
        if (dirHandle?.name) {
          onFolderPathChange(dirHandle.name);
        }
        const selectedFiles: File[] = [];
        if (typeof dirHandle.values === 'function') {
          for await (const entry of dirHandle.values()) {
            if (entry.kind === 'file' && typeof entry.getFile === 'function') {
              const file = await entry.getFile();
              if (/\.(jpe?g|png|webp|tiff?|gif|bmp)$/i.test(file.name)) {
                selectedFiles.push(file);
              }
            }
          }
        }
        if (selectedFiles.length > 0) {
          onUploadedFilesChange(selectedFiles);
        }
        return;
      } catch (err: any) {
        if (err.name === 'AbortError') return;
      }
    }
    folderInputRef.current?.click();
  };

  const handleFallbackDirectoryChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      const firstFile = files[0];
      const relativePath = (firstFile as any).webkitRelativePath || '';
      const folderName = relativePath.split('/')[0] || firstFile.name || '';
      if (folderName) {
        onFolderPathChange(folderName);
      }
      const imageFiles = Array.from(files).filter((f) =>
        /\.(jpe?g|png|webp|tiff?|gif|bmp)$/i.test(f.name)
      );
      if (imageFiles.length > 0) {
        onUploadedFilesChange(imageFiles);
      }
    }
    e.target.value = '';
  };

  useEffect(() => {
    let isMounted = true;
    const fetchStatuses = async () => {
      try {
        const list = await api.listSourceProviders();
        if (isMounted && Array.isArray(list)) {
          const map: Record<string, SourceConnectionStatus> = {};
          list.forEach((p) => {
            map[p.provider_id] = p;
          });
          setProviderStatuses(map);
        }
      } catch {
        // Fallback gracefully without breaking UI
      }
    };
    fetchStatuses();
    return () => {
      isMounted = false;
    };
  }, []);

  const handleConnectProvider = async (providerId: 'google_photos' | 'google_drive') => {
    setIsConnecting(providerId);
    setAuthError(null);
    try {
      const scope =
        providerId === 'google_photos'
          ? GOOGLE_SCOPES.PHOTOS_INGEST
          : GOOGLE_SCOPES.DRIVE_INGEST;
      const token = await requestGoogleAccessToken({ scope });
      const updated = await api.connectSourceProvider(providerId, {
        provider_id: providerId,
        auth_token: token,
      });
      setProviderStatuses((prev) => ({
        ...prev,
        [providerId]: updated || {
          provider_id: providerId,
          name: providerId === 'google_photos' ? 'Google Photos' : 'Google Drive',
          description: '',
          connected: true,
          auth_required: false,
          authenticated: true,
          message: 'Connected',
          capabilities: prev[providerId]?.capabilities || {
            supports_containers: true,
            supports_search: true,
            supports_thumbnails: true,
            supports_pagination: true,
            supports_streaming: true,
            requires_auth: true,
          },
        },
      }));
    } catch (err: any) {
      setAuthError(
        err.message ||
          `Failed to authenticate ${
            providerId === 'google_photos' ? 'Google Photos' : 'Google Drive'
          }`
      );
    } finally {
      setIsConnecting(null);
    }
  };

  const activeProviderForModal =
    sourceType === 'google_photos'
      ? 'google_photos'
      : sourceType === 'google_drive'
      ? 'google_drive'
      : sourceType === 's3'
      ? 's3'
      : 'gcs';

  const activeProviderName =
    sourceType === 'google_photos'
      ? 'Google Photos'
      : sourceType === 'google_drive'
      ? 'Google Drive'
      : sourceType === 's3'
      ? 'Amazon S3'
      : 'Google Cloud Storage';

  const currentStatus = providerStatuses[sourceType];
  const isConnected = currentStatus ? currentStatus.connected : true;
  const authRequired = currentStatus ? currentStatus.auth_required : false;

  return (
    <div className="flex flex-col gap-5">
      {/* Top Main Ingestion Source Switcher */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {/* 1. Local / Uploads */}
        <button
          type="button"
          onClick={() => {
            onSourceTypeChange('local');
            onSelectContainer(null);
          }}
          disabled={disabled}
          className={`flex items-center gap-3 p-4 rounded-xl border transition-all text-left cursor-pointer ${
            sourceType === 'local'
              ? 'border-blue-600 bg-blue-50/50 dark:bg-blue-950/30 ring-2 ring-blue-500/20 text-blue-700 dark:text-blue-300'
              : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300'
          }`}
        >
          <div className={`p-2.5 rounded-lg ${sourceType === 'local' ? 'bg-blue-600 text-white' : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400'}`}>
            <FolderOpen className="w-5 h-5" />
          </div>
          <div>
            <p className="font-semibold text-sm">Local Folder / Uploads</p>
            <p className="text-xs text-slate-500 dark:text-slate-400">Local directory scans or drag & drop</p>
          </div>
        </button>

        {/* 2. Google Photos */}
        <button
          type="button"
          onClick={() => {
            onSourceTypeChange('google_photos');
            onSelectContainer(null);
          }}
          disabled={disabled}
          className={`flex items-center gap-3 p-4 rounded-xl border transition-all text-left cursor-pointer ${
            sourceType === 'google_photos'
              ? 'border-blue-600 bg-blue-50/50 dark:bg-blue-950/30 ring-2 ring-blue-500/20 text-blue-700 dark:text-blue-300'
              : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300'
          }`}
        >
          <div className={`p-2.5 rounded-lg ${sourceType === 'google_photos' ? 'bg-blue-600 text-white' : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400'}`}>
            <Camera className="w-5 h-5" />
          </div>
          <div>
            <p className="font-semibold text-sm">Google Photos</p>
            <p className="text-xs text-slate-500 dark:text-slate-400">Albums & food category filters</p>
          </div>
        </button>

        {/* 3. Cloud Drive & Storage */}
        <button
          type="button"
          onClick={() => {
            onSourceTypeChange('google_drive');
            onSelectContainer(null);
          }}
          disabled={disabled}
          className={`flex items-center gap-3 p-4 rounded-xl border transition-all text-left cursor-pointer ${
            sourceType === 'google_drive' || sourceType === 's3' || sourceType === 'gcs'
              ? 'border-blue-600 bg-blue-50/50 dark:bg-blue-950/30 ring-2 ring-blue-500/20 text-blue-700 dark:text-blue-300'
              : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300'
          }`}
        >
          <div className={`p-2.5 rounded-lg ${sourceType === 'google_drive' || sourceType === 's3' || sourceType === 'gcs' ? 'bg-blue-600 text-white' : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400'}`}>
            <Cloud className="w-5 h-5" />
          </div>
          <div>
            <p className="font-semibold text-sm">Cloud Drive & Storage</p>
            <p className="text-xs text-slate-500 dark:text-slate-400">Google Drive, S3, or GCS</p>
          </div>
        </button>
      </div>

      {/* Specific Source Configuration Panel */}
      {sourceType === 'local' && (
        <div className="flex flex-col gap-4">
          <div className="flex border-b border-slate-200 dark:border-slate-800">
            <button
              type="button"
              onClick={() => onLocalMethodChange('folder')}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-semibold border-b-2 transition-all cursor-pointer ${
                localMethod === 'folder'
                  ? 'border-blue-600 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
              }`}
            >
              <FolderOpen className="w-4 h-4" />
              Scans Folder Path
            </button>
            <button
              type="button"
              onClick={() => onLocalMethodChange('files')}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-semibold border-b-2 transition-all cursor-pointer ${
                localMethod === 'files'
                  ? 'border-blue-600 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
              }`}
            >
              <Upload className="w-4 h-4" />
              Direct File Upload
            </button>
          </div>

          {localMethod === 'folder' ? (
            <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 shadow-xs flex flex-col gap-3">
              <label htmlFor="local-scans-path" className="text-sm font-semibold text-slate-800 dark:text-slate-200">
                Local Scans Directory Path
              </label>
              <div className="flex gap-2">
                <input
                  id="local-scans-path"
                  aria-label="Local Scans Directory Path"
                  type="text"
                  placeholder={getOsPathPlaceholder()}
                  value={folderPath}
                  onChange={(e) => onFolderPathChange(e.target.value)}
                  disabled={disabled}
                  className="flex-1 px-3.5 py-2 text-sm bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono"
                />
                <Button
                  type="button"
                  variant="secondary"
                  onClick={handleBrowseDirectory}
                  disabled={disabled}
                  leftIcon={<FolderOpen className="w-4 h-4" />}
                >
                  Browse Folder...
                </Button>
                <input
                  ref={folderInputRef}
                  type="file"
                  // @ts-ignore
                  webkitdirectory=""
                  directory=""
                  multiple
                  className="hidden"
                  onChange={handleFallbackDirectoryChange}
                  tabIndex={-1}
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
                onFilesChange={onUploadedFilesChange}
                disabled={disabled}
              />
            </div>
          )}
        </div>
      )}

      {sourceType === 'google_photos' && (
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 shadow-xs flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Camera className="w-5 h-5 text-blue-600" />
              <span className="font-semibold text-sm text-slate-900 dark:text-slate-100">
                Google Photos Library Connection
              </span>
            </div>
            <div className="flex items-center gap-2">
              {isConnecting === 'google_photos' ? (
                <Badge variant="neutral" size="sm">
                  <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
                  Connecting...
                </Badge>
              ) : authRequired && !isConnected ? (
                <div className="flex items-center gap-2">
                  <Badge variant="warning" size="sm">
                    <AlertCircle className="w-3.5 h-3.5 mr-1" />
                    Auth Required
                  </Badge>
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    onClick={() => handleConnectProvider('google_photos')}
                    disabled={disabled || isConnecting !== null}
                    aria-label="Connect Google Photos"
                  >
                    Connect Account
                  </Button>
                </div>
              ) : (
                <Badge variant="success" size="sm">
                  <CheckCircle2 className="w-3.5 h-3.5 mr-1" />
                  Ready
                </Badge>
              )}
            </div>
          </div>

          {authError && (
            <div className="p-3 rounded-lg bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 flex items-center gap-2 text-xs text-rose-800 dark:text-rose-200">
              <AlertCircle className="w-4 h-4 text-rose-600 shrink-0" />
              <span className="flex-1">{authError}</span>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {/* Category Filter */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-semibold text-slate-700 dark:text-slate-300">
                Category Search Filter
              </label>
              <select
                value={categoryFilter}
                onChange={(e) => onCategoryFilterChange(e.target.value)}
                disabled={disabled}
                className="px-3 py-2 text-sm bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="FOOD">FOOD — Recipes, Food & Kitchen Photos</option>
                <option value="DOCUMENTS">DOCUMENTS — Scanned Documents & Card Clippings</option>
                <option value="RECEIPTS">RECEIPTS — Receipts & Handwritten Notes</option>
                <option value="ALL">ALL — Full Library Search</option>
              </select>
            </div>

            {/* Album Selection Button / Selected Card */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-semibold text-slate-700 dark:text-slate-300">
                Selected Album / Collection
              </label>
              <div className="flex items-center gap-2">
                {selectedContainer ? (
                  <div className="flex-1 flex items-center justify-between px-3 py-2 bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-800 rounded-lg">
                    <span className="text-xs font-semibold text-blue-800 dark:text-blue-300 truncate">
                      {selectedContainer.name}
                    </span>
                    <button
                      type="button"
                      onClick={() => onSelectContainer(null)}
                      className="text-xs text-blue-600 hover:text-blue-800 dark:hover:text-blue-200 ml-2"
                    >
                      Clear
                    </button>
                  </div>
                ) : (
                  <Button
                    type="button"
                    variant="secondary"
                    className="w-full"
                    onClick={() => setIsModalOpen(true)}
                    disabled={disabled}
                    leftIcon={<Folder className="w-4 h-4" />}
                  >
                    Browse Google Photos Albums...
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {(sourceType === 'google_drive' || sourceType === 's3' || sourceType === 'gcs') && (
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 shadow-xs flex flex-col gap-4">
          {/* Cloud Sub-type Switcher */}
          <div className="flex gap-2 border-b border-slate-200 dark:border-slate-800 pb-3">
            <button
              type="button"
              onClick={() => {
                onSourceTypeChange('google_drive');
                onSelectContainer(null);
              }}
              className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-all ${
                sourceType === 'google_drive'
                  ? 'bg-blue-600 text-white'
                  : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-200'
              }`}
            >
              Google Drive
            </button>
            <button
              type="button"
              onClick={() => {
                onSourceTypeChange('s3');
                onSelectContainer(null);
              }}
              className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-all ${
                sourceType === 's3'
                  ? 'bg-blue-600 text-white'
                  : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-200'
              }`}
            >
              Amazon S3 / MinIO
            </button>
            <button
              type="button"
              onClick={() => {
                onSourceTypeChange('gcs');
                onSelectContainer(null);
              }}
              className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-all ${
                sourceType === 'gcs'
                  ? 'bg-blue-600 text-white'
                  : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-200'
              }`}
            >
              Google Cloud Storage
            </button>
          </div>

          {sourceType === 'google_drive' && (
            <div className="flex flex-col gap-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Cloud className="w-5 h-5 text-blue-600" />
                  <span className="font-semibold text-sm text-slate-900 dark:text-slate-100">
                    Google Drive Connection
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {isConnecting === 'google_drive' ? (
                    <Badge variant="neutral" size="sm">
                      <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
                      Connecting...
                    </Badge>
                  ) : authRequired && !isConnected ? (
                    <div className="flex items-center gap-2">
                      <Badge variant="warning" size="sm">
                        <AlertCircle className="w-3.5 h-3.5 mr-1" />
                        Auth Required
                      </Badge>
                      <Button
                        type="button"
                        variant="primary"
                        size="sm"
                        onClick={() => handleConnectProvider('google_drive')}
                        disabled={disabled || isConnecting !== null}
                        aria-label="Connect Google Drive"
                      >
                        Connect Account
                      </Button>
                    </div>
                  ) : (
                    <Badge variant="success" size="sm">
                      <CheckCircle2 className="w-3.5 h-3.5 mr-1" />
                      Ready
                    </Badge>
                  )}
                </div>
              </div>

              {authError && (
                <div className="p-3 rounded-lg bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 flex items-center gap-2 text-xs text-rose-800 dark:text-rose-200">
                  <AlertCircle className="w-4 h-4 text-rose-600 shrink-0" />
                  <span className="flex-1">{authError}</span>
                </div>
              )}

              <div className="flex flex-col gap-3">
                <label className="text-xs font-semibold text-slate-700 dark:text-slate-300">
                  Target Google Drive Folder
                </label>
                {selectedContainer ? (
                  <div className="flex items-center justify-between p-3 bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-800 rounded-lg">
                    <div className="flex items-center gap-2">
                      <Folder className="w-4 h-4 text-blue-600" />
                      <span className="text-sm font-medium text-blue-900 dark:text-blue-200">
                        {selectedContainer.name}
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => onSelectContainer(null)}
                      className="text-xs text-blue-600 hover:text-blue-800 font-semibold"
                    >
                      Change Folder
                    </button>
                  </div>
                ) : (
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => setIsModalOpen(true)}
                    disabled={disabled}
                    leftIcon={<Folder className="w-4 h-4" />}
                  >
                    Browse &amp; Select Google Drive Folder...
                  </Button>
                )}
              </div>
            </div>
          )}

          {(sourceType === 's3' || sourceType === 'gcs') && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-slate-700 dark:text-slate-300">
                  {sourceType === 's3' ? 'S3 Bucket Name' : 'GCS Bucket Name'}
                </label>
                <input
                  type="text"
                  placeholder={sourceType === 's3' ? 'recipeboxer-scans' : 'recipeboxer-archive'}
                  value={bucketName}
                  onChange={(e) => onBucketNameChange(e.target.value)}
                  disabled={disabled}
                  className="px-3 py-2 text-sm bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 font-mono"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-slate-700 dark:text-slate-300">
                  Folder / Key Prefix (Optional)
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    placeholder="batch-2026/box-1/"
                    value={prefix}
                    onChange={(e) => onPrefixChange(e.target.value)}
                    disabled={disabled}
                    className="flex-1 px-3 py-2 text-sm bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 font-mono"
                  />
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => setIsModalOpen(true)}
                    disabled={disabled}
                  >
                    Browse
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Album / Container Picker Dialog */}
      <AlbumPickerModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        providerId={activeProviderForModal}
        providerName={activeProviderName}
        selectedContainerId={selectedContainer?.id}
        onSelectContainer={(container) => {
          onSelectContainer(container);
          if (sourceType === 's3' || sourceType === 'gcs') {
            onPrefixChange(container.id);
          }
        }}
      />
    </div>
  );
};
