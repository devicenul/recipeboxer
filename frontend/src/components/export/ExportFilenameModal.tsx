import React, { useState, useEffect } from 'react';
import { FileEdit, Download, X, Check } from 'lucide-react';
import { Button } from '../common/Button';

export interface ExportFilenameModalProps {
  isOpen: boolean;
  filename: string;
  formatName: string;
  onSave: (newFilename: string) => void;
  onClose: () => void;
  onDownload: (filename: string) => void;
}

export const ExportFilenameModal: React.FC<ExportFilenameModalProps> = ({
  isOpen,
  filename,
  formatName,
  onSave,
  onClose,
  onDownload,
}) => {
  const [currentFilename, setCurrentFilename] = useState(filename);

  useEffect(() => {
    setCurrentFilename(filename);
  }, [filename, isOpen]);

  if (!isOpen) return null;

  const handleSave = () => {
    const trimmed = currentFilename.trim() || filename;
    onSave(trimmed);
    onClose();
  };

  const handleDownload = () => {
    const trimmed = currentFilename.trim() || filename;
    onSave(trimmed);
    onDownload(trimmed);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-xs animate-in fade-in duration-150">
      <div
        className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-2xl max-w-md w-full flex flex-col gap-5"
        role="dialog"
        aria-modal="true"
        aria-labelledby="filename-modal-title"
      >
        {/* Header */}
        <div className="flex items-center justify-between pb-3 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-2 text-slate-900 dark:text-slate-100 font-bold text-sm">
            <FileEdit className="w-4 h-4 text-emerald-500" />
            <h3 id="filename-modal-title">Customize Export Filename</h3>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors cursor-pointer"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex flex-col gap-2">
          <label
            htmlFor="custom-filename-input"
            className="text-xs font-semibold text-slate-700 dark:text-slate-300"
          >
            Filename for {formatName}
          </label>
          <input
            id="custom-filename-input"
            type="text"
            value={currentFilename}
            onChange={(e) => setCurrentFilename(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                handleDownload();
              }
            }}
            autoFocus
            className="w-full px-3 py-2 text-xs font-mono rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-emerald-500"
            placeholder="Enter custom filename..."
          />
          <p className="text-[11px] text-slate-500 dark:text-slate-400">
            Special characters and path separators will be automatically sanitized upon export.
          </p>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-100 dark:border-slate-800">
          <Button variant="outline" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={handleSave}
            leftIcon={<Check className="w-3.5 h-3.5" />}
          >
            Apply
          </Button>
          <Button
            variant="success"
            size="sm"
            onClick={handleDownload}
            leftIcon={<Download className="w-3.5 h-3.5" />}
          >
            Download
          </Button>
        </div>
      </div>
    </div>
  );
};
