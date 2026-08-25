import React, { useState, useRef } from 'react';
import { UploadCloud, Image as ImageIcon, X } from 'lucide-react';

export interface DropzoneProps {
  files: File[];
  onFilesChange: (files: File[]) => void;
  maxFiles?: number;
  disabled?: boolean;
}

export const Dropzone: React.FC<DropzoneProps> = ({
  files,
  onFilesChange,
  maxFiles = 500,
  disabled = false,
}) => {
  const [isDragOver, setIsDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    if (!disabled) setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (disabled) return;

    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const droppedFiles = Array.from(e.dataTransfer.files).filter((file) =>
        /\.(jpe?g|png|webp|tiff?)$/i.test(file.name)
      );
      if (droppedFiles.length > 0) {
        onFilesChange([...files, ...droppedFiles].slice(0, maxFiles));
      }
    }
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const selected = Array.from(e.target.files).filter((file) =>
        /\.(jpe?g|png|webp|tiff?)$/i.test(file.name)
      );
      onFilesChange([...files, ...selected].slice(0, maxFiles));
    }
  };

  const removeFile = (index: number) => {
    onFilesChange(files.filter((_, i) => i !== index));
  };

  return (
    <div className="flex flex-col gap-3">
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => !disabled && fileInputRef.current?.click()}
        className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-all flex flex-col items-center justify-center gap-3 ${
          isDragOver
            ? 'border-blue-500 bg-blue-50/50 dark:bg-blue-950/30'
            : 'border-slate-300 dark:border-slate-700 hover:border-slate-400 dark:hover:border-slate-600 bg-slate-50/50 dark:bg-slate-900/40'
        } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".jpg,.jpeg,.png,.webp,.tif,.tiff"
          onChange={handleFileInputChange}
          className="hidden"
          disabled={disabled}
        />
        <div className="p-3 bg-blue-100 dark:bg-blue-950/60 text-blue-600 dark:text-blue-400 rounded-full">
          <UploadCloud className="w-6 h-6" />
        </div>
        <div>
          <p className="text-sm font-medium text-slate-800 dark:text-slate-200">
            <span className="text-blue-600 dark:text-blue-400">Click to upload</span> or drag and drop card scans
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
            JPG, PNG, WebP, TIFF (up to {maxFiles} files)
          </p>
        </div>
      </div>

      {files.length > 0 && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between text-xs text-slate-600 dark:text-slate-400">
            <span>Selected scans ({files.length})</span>
            <button
              onClick={() => onFilesChange([])}
              className="text-rose-600 dark:text-rose-400 hover:underline cursor-pointer"
            >
              Clear all
            </button>
          </div>
          <div className="max-h-36 overflow-y-auto grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2 pr-1">
            {files.map((file, idx) => (
              <div
                key={`${file.name}-${idx}`}
                className="flex items-center justify-between p-2 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 text-xs shadow-xs group"
              >
                <div className="flex items-center gap-1.5 min-w-0">
                  <ImageIcon className="w-4 h-4 text-slate-400 shrink-0" />
                  <span className="truncate text-slate-700 dark:text-slate-300" title={file.name}>
                    {file.name}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeFile(idx);
                  }}
                  className="text-slate-400 hover:text-rose-600 dark:hover:text-rose-400 p-0.5"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
