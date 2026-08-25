import React, { useState } from 'react';
import {
  Download,
  FileSpreadsheet,
  Code2,
  FileJson,
  ChefHat,
  FileText,
  BookOpen,
  FileCode,
  Flame,
  Printer,
} from 'lucide-react';
import { RecipeSchema } from '../../types/recipe';
import { CsvPreview } from './CsvPreview';
import { JsonLdPreview } from './JsonLdPreview';
import { RawJsonPreview } from './RawJsonPreview';
import { MarkdownPreview } from './MarkdownPreview';
import { CooklangPreview } from './CooklangPreview';
import { GoogleDocsPreview } from './GoogleDocsPreview';
import { BinaryExportPreview } from './BinaryExportPreview';
import { Button } from '../common/Button';
import { downloadPlanToEatCsv } from '../../utils/exportPlanToEat';
import { downloadJsonLd } from '../../utils/exportJsonLd';
import { downloadRawJson } from '../../utils/exportJson';
import { downloadMarkdown } from '../../utils/exportMarkdown';
import { downloadCooklang } from '../../utils/exportCooklang';
import { downloadGoogleDocsHtml } from '../../utils/exportGoogleDocs';
import { downloadDocx, downloadPdf, downloadPaprika } from '../../utils/exportBinary';

export interface ExportDialogProps {
  recipes: RecipeSchema[];
  sessionId?: string;
}

export type ExportTab =
  | 'csv'
  | 'jsonld'
  | 'raw'
  | 'markdown'
  | 'cooklang'
  | 'paprika'
  | 'googledocs'
  | 'docx'
  | 'pdf';

export const ExportDialog: React.FC<ExportDialogProps> = ({ recipes, sessionId }) => {
  const [activeTab, setActiveTab] = useState<ExportTab>('csv');

  if (!recipes || recipes.length === 0) {
    return (
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-12 text-center flex flex-col items-center gap-3">
        <ChefHat className="w-12 h-12 text-slate-400" />
        <h3 className="text-base font-semibold text-slate-800 dark:text-slate-200">
          No Recipes Available for Export
        </h3>
        <p className="text-xs text-slate-500 dark:text-slate-400 max-w-sm">
          Run a scan and complete the HITL review queue to generate extracted recipes for export.
        </p>
      </div>
    );
  }

  const handleDownload = () => {
    const filenameSuffix = sessionId ? `_${sessionId.slice(-6)}` : '';
    switch (activeTab) {
      case 'csv':
        downloadPlanToEatCsv(recipes, `recipeboxer_export${filenameSuffix}.csv`);
        break;
      case 'jsonld':
        downloadJsonLd(recipes, `recipeboxer_json_ld${filenameSuffix}.json`);
        break;
      case 'raw':
        downloadRawJson(recipes, `recipeboxer_recipes${filenameSuffix}.json`);
        break;
      case 'markdown':
        downloadMarkdown(recipes, `recipeboxer_recipes${filenameSuffix}.md`);
        break;
      case 'cooklang':
        downloadCooklang(recipes, `recipeboxer_recipes${filenameSuffix}.cook`);
        break;
      case 'googledocs':
        downloadGoogleDocsHtml(recipes, `recipeboxer_google_docs${filenameSuffix}.html`);
        break;
      case 'paprika':
        downloadPaprika(sessionId, `recipeboxer_export${filenameSuffix}.paprikarecipes`);
        break;
      case 'docx':
        downloadDocx(sessionId, `recipeboxer_cookbook${filenameSuffix}.docx`);
        break;
      case 'pdf':
        downloadPdf(sessionId, `recipeboxer_cookbook${filenameSuffix}.pdf`);
        break;
    }
  };

  const getActiveTabTitle = (): string => {
    switch (activeTab) {
      case 'csv': return 'Plan To Eat CSV';
      case 'jsonld': return 'Schema.org JSON-LD';
      case 'raw': return 'Raw Recipe JSON';
      case 'markdown': return 'Obsidian Markdown';
      case 'cooklang': return 'Cooklang (.cook)';
      case 'googledocs': return 'Google Docs Archive';
      case 'paprika': return 'Paprika Archive';
      case 'docx': return 'DOCX Cookbook';
      case 'pdf': return 'Printable PDF';
    }
  };

  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-6 shadow-xs flex flex-col gap-6" data-tour="export-matrix">
      {/* Top Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-slate-200 dark:border-slate-800">
        <div>
          <h2 className="text-base font-bold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Download className="w-5 h-5 text-emerald-500" />
            <span>Multi-Format Recipe Export & Archival Publishing</span>
          </h2>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Export {recipes.length} digitized recipes into culinary managers, documents, and archival formats.
          </p>
        </div>

        <Button
          variant="success"
          size="md"
          onClick={handleDownload}
          leftIcon={<Download className="w-4 h-4" />}
        >
          Download {getActiveTabTitle()}
        </Button>
      </div>

      {/* Group 1: Primary Formats (R0) */}
      <div className="flex flex-col gap-2">
        <span className="text-[11px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
          Primary Formats (RFC 4180 & Schema.org)
        </span>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {/* Plan To Eat CSV */}
          <button
            type="button"
            onClick={() => setActiveTab('csv')}
            className={`p-3.5 rounded-xl border text-left flex flex-col gap-1 transition-all cursor-pointer ${
              activeTab === 'csv'
                ? 'border-emerald-500 bg-emerald-50/50 dark:bg-emerald-950/30 ring-2 ring-emerald-500/20'
                : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-slate-50/50 dark:bg-slate-850/50'
            }`}
          >
            <div className="flex items-center gap-2 font-semibold text-xs text-emerald-700 dark:text-emerald-400">
              <FileSpreadsheet className="w-4 h-4" />
              <span>Plan To Eat CSV</span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              RFC 4180 compliant 9-column CSV import.
            </p>
          </button>

          {/* Schema.org JSON-LD */}
          <button
            type="button"
            onClick={() => setActiveTab('jsonld')}
            className={`p-3.5 rounded-xl border text-left flex flex-col gap-1 transition-all cursor-pointer ${
              activeTab === 'jsonld'
                ? 'border-blue-500 bg-blue-50/50 dark:bg-blue-950/30 ring-2 ring-blue-500/20'
                : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-slate-50/50 dark:bg-slate-850/50'
            }`}
          >
            <div className="flex items-center gap-2 font-semibold text-xs text-blue-700 dark:text-blue-400">
              <Code2 className="w-4 h-4" />
              <span>Schema.org JSON-LD</span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              Web standard structured recipe microdata.
            </p>
          </button>

          {/* Raw JSON */}
          <button
            type="button"
            onClick={() => setActiveTab('raw')}
            className={`p-3.5 rounded-xl border text-left flex flex-col gap-1 transition-all cursor-pointer ${
              activeTab === 'raw'
                ? 'border-purple-500 bg-purple-50/50 dark:bg-purple-950/30 ring-2 ring-purple-500/20'
                : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-slate-50/50 dark:bg-slate-850/50'
            }`}
          >
            <div className="flex items-center gap-2 font-semibold text-xs text-purple-700 dark:text-purple-400">
              <FileJson className="w-4 h-4" />
              <span>Standard Raw JSON</span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              Lossless RecipeSchema JSON array backup.
            </p>
          </button>
        </div>
      </div>

      {/* Group 2: Culinary Ecosystem Serializers (R3) */}
      <div className="flex flex-col gap-2">
        <span className="text-[11px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
          Culinary Ecosystem Serializers
        </span>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {/* Obsidian Markdown */}
          <button
            type="button"
            onClick={() => setActiveTab('markdown')}
            className={`p-3.5 rounded-xl border text-left flex flex-col gap-1 transition-all cursor-pointer ${
              activeTab === 'markdown'
                ? 'border-purple-500 bg-purple-50/50 dark:bg-purple-950/30 ring-2 ring-purple-500/20'
                : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-slate-50/50 dark:bg-slate-850/50'
            }`}
          >
            <div className="flex items-center gap-2 font-semibold text-xs text-purple-700 dark:text-purple-400">
              <FileText className="w-4 h-4" />
              <span>Obsidian Markdown</span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              YAML frontmatter & Obsidian recipe view links.
            </p>
          </button>

          {/* Cooklang */}
          <button
            type="button"
            onClick={() => setActiveTab('cooklang')}
            className={`p-3.5 rounded-xl border text-left flex flex-col gap-1 transition-all cursor-pointer ${
              activeTab === 'cooklang'
                ? 'border-amber-500 bg-amber-50/50 dark:bg-amber-950/30 ring-2 ring-amber-500/20'
                : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-slate-50/50 dark:bg-slate-850/50'
            }`}
          >
            <div className="flex items-center gap-2 font-semibold text-xs text-amber-700 dark:text-amber-400">
              <FileCode className="w-4 h-4" />
              <span>Cooklang (.cook)</span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              @ingredient{}, #cookware{}, and ~timer{} syntax.
            </p>
          </button>

          {/* Paprika */}
          <button
            type="button"
            onClick={() => setActiveTab('paprika')}
            className={`p-3.5 rounded-xl border text-left flex flex-col gap-1 transition-all cursor-pointer ${
              activeTab === 'paprika'
                ? 'border-orange-500 bg-orange-50/50 dark:bg-orange-950/30 ring-2 ring-orange-500/20'
                : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-slate-50/50 dark:bg-slate-850/50'
            }`}
          >
            <div className="flex items-center gap-2 font-semibold text-xs text-orange-700 dark:text-orange-400">
              <Flame className="w-4 h-4" />
              <span>Paprika (.paprikarecipes)</span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              Gzip archive with base64 embedded card photos.
            </p>
          </button>
        </div>
      </div>

      {/* Group 3: Archival Documents (R1, R2) */}
      <div className="flex flex-col gap-2">
        <span className="text-[11px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
          Archival Document Generation & Cookbooks
        </span>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {/* Google Docs */}
          <button
            type="button"
            onClick={() => setActiveTab('googledocs')}
            className={`p-3.5 rounded-xl border text-left flex flex-col gap-1 transition-all cursor-pointer ${
              activeTab === 'googledocs'
                ? 'border-emerald-600 bg-emerald-50/50 dark:bg-emerald-950/30 ring-2 ring-emerald-600/20'
                : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-slate-50/50 dark:bg-slate-850/50'
            }`}
          >
            <div className="flex items-center gap-2 font-semibold text-xs text-emerald-800 dark:text-emerald-300">
              <BookOpen className="w-4 h-4" />
              <span>Google Docs Archival</span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              Structured batch update requests & HTML archive.
            </p>
          </button>

          {/* Offline DOCX */}
          <button
            type="button"
            onClick={() => setActiveTab('docx')}
            className={`p-3.5 rounded-xl border text-left flex flex-col gap-1 transition-all cursor-pointer ${
              activeTab === 'docx'
                ? 'border-blue-600 bg-blue-50/50 dark:bg-blue-950/30 ring-2 ring-blue-600/20'
                : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-slate-50/50 dark:bg-slate-850/50'
            }`}
          >
            <div className="flex items-center gap-2 font-semibold text-xs text-blue-800 dark:text-blue-300">
              <FileText className="w-4 h-4" />
              <span>Heirloom DOCX</span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              Standalone Word cookbook with card scans.
            </p>
          </button>

          {/* Printable PDF */}
          <button
            type="button"
            onClick={() => setActiveTab('pdf')}
            className={`p-3.5 rounded-xl border text-left flex flex-col gap-1 transition-all cursor-pointer ${
              activeTab === 'pdf'
                ? 'border-rose-600 bg-rose-50/50 dark:bg-rose-950/30 ring-2 ring-rose-600/20'
                : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-slate-50/50 dark:bg-slate-850/50'
            }`}
          >
            <div className="flex items-center gap-2 font-semibold text-xs text-rose-800 dark:text-rose-300">
              <Printer className="w-4 h-4" />
              <span>Printable PDF</span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              Print-ready cookbook with header/footer page numbers.
            </p>
          </button>
        </div>
      </div>

      {/* Format Content Preview */}
      <div className="pt-2">
        {activeTab === 'csv' && <CsvPreview recipes={recipes} />}
        {activeTab === 'jsonld' && <JsonLdPreview recipes={recipes} />}
        {activeTab === 'raw' && <RawJsonPreview recipes={recipes} />}
        {activeTab === 'markdown' && <MarkdownPreview recipes={recipes} />}
        {activeTab === 'cooklang' && <CooklangPreview recipes={recipes} />}
        {activeTab === 'googledocs' && <GoogleDocsPreview recipes={recipes} />}
        {activeTab === 'paprika' && (
          <BinaryExportPreview
            format="paprika"
            recipes={recipes}
            onDownload={handleDownload}
          />
        )}
        {activeTab === 'docx' && (
          <BinaryExportPreview
            format="docx"
            recipes={recipes}
            onDownload={handleDownload}
          />
        )}
        {activeTab === 'pdf' && (
          <BinaryExportPreview
            format="pdf"
            recipes={recipes}
            onDownload={handleDownload}
          />
        )}
      </div>
    </div>
  );
};
