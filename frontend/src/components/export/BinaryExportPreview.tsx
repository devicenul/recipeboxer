import React from 'react';
import { FileText, Download, CheckCircle, Sparkles } from 'lucide-react';
import { RecipeSchema } from '../../types/recipe';

export interface BinaryExportPreviewProps {
  format: 'docx' | 'pdf' | 'paprika' | 'project';
  recipes: RecipeSchema[];
  onDownload: () => void;
}

export const BinaryExportPreview: React.FC<BinaryExportPreviewProps> = ({
  format,
  recipes,
  onDownload,
}) => {
  const formatInfo = {
    docx: {
      title: 'Heirloom Cookbook DOCX',
      desc: 'Standard OpenXML (.docx) family cookbook document with styled headings, metadata tables, numbered directions, and embedded card scan photos.',
      features: [
        'Page breaks between recipes for clean multi-page document',
        'Styled heirloom typography (Georgia / Calibri)',
        'Metadata table summary (Yield, Prep Time, Cook Time, Total Time)',
        'Embedded high-resolution card scan photos with figure captions',
        'Fully compatible with Microsoft Word, Google Docs, Apple Pages, and LibreOffice',
      ],
      tag: 'Office / Print Document',
      color: 'text-blue-600 dark:text-blue-400',
    },
    pdf: {
      title: 'Printable Archival PDF Cookbook',
      desc: 'High-resolution, print-ready PDF cookbook documents with elegant heirloom typography, running headers/footers, and page numbers.',
      features: [
        'Print-ready US Letter pages with clean margins and pagination',
        'Running headers and footers with "Page X of Y" numbering',
        'Styled metadata info boxes and shaded accent borders',
        'Side-by-side or stacked card scan images embedded directly',
        'Pure-vector layout ready for instant high-quality home or commercial printing',
      ],
      tag: 'Print Ready PDF',
      color: 'text-rose-600 dark:text-rose-400',
    },
    paprika: {
      title: 'Paprika Recipe Manager (.paprikarecipes)',
      desc: 'Gzip-compressed .paprikarecipes archive containing individual JSON payloads and base64-encoded card photos for 1-click Paprika app import.',
      features: [
        '1-click direct import into Paprika 3 (iOS, macOS, Android, Windows)',
        'Embeds original scanned card photos directly inside recipe records',
        'Automatic ingredient, instruction, yield, time, and category mapping',
        'Preserves vintage attribution and card notes',
      ],
      tag: 'Mobile & Desktop App Archive',
      color: 'text-amber-600 dark:text-amber-400',
    },
    project: {
      title: '.recipeboxer Project Archive',
      desc: 'Standardized, compressed ZIP project container preserving complete session state: manifest JSON, visual fingerprints, correlation proposals, transcribed recipes, step bindings, marginalia, and original card scan image assets.',
      features: [
        'Complete lossless session snapshot (manifest.json + assets/scans/)',
        'Content-addressed SHA-256 image scans with zero duplicate storage',
        'Preserves visual fingerprints, paper color, ink, stains, and boundary text',
        'Retains HITL review decisions, sequence ordering, and reviewer notes',
        '100% portable across machines, collaborators, and cloud environments',
      ],
      tag: 'Portable Project State Container',
      color: 'text-emerald-600 dark:text-emerald-400',
    },
  }[format];

  return (
    <div className="border border-slate-200 dark:border-slate-800 rounded-xl p-6 bg-slate-50/50 dark:bg-slate-850/50 flex flex-col gap-4">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="p-3 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-xs">
            <FileText className={`w-6 h-6 ${formatInfo.color}`} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">
                {formatInfo.title}
              </h3>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-300 font-medium">
                {formatInfo.tag}
              </span>
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 max-w-xl">
              {formatInfo.desc}
            </p>
          </div>
        </div>

        <button
          onClick={onDownload}
          className="inline-flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-lg shadow-sm transition-colors cursor-pointer"
        >
          <Download className="w-4 h-4" />
          <span>Download {format.toUpperCase()}</span>
        </button>
      </div>

      <div className="border-t border-slate-200 dark:border-slate-800 pt-4">
        <h4 className="text-xs font-semibold text-slate-700 dark:text-slate-300 mb-2 flex items-center gap-1.5">
          <Sparkles className="w-3.5 h-3.5 text-amber-500" />
          <span>Archival Features Included:</span>
        </h4>
        <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs text-slate-600 dark:text-slate-400">
          {formatInfo.features.map((feat, idx) => (
            <li key={idx} className="flex items-start gap-2">
              <CheckCircle className="w-3.5 h-3.5 text-emerald-500 shrink-0 mt-0.5" />
              <span>{feat}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-3 text-xs text-slate-500 dark:text-slate-400 flex items-center justify-between">
        <span>Ready to package <strong>{recipes.length}</strong> recipe(s) with high-fidelity metadata.</span>
        <span className="text-[11px] font-mono text-slate-400">Status: Ready for Generation</span>
      </div>
    </div>
  );
};
