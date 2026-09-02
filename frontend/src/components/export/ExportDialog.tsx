import React, { useState, useEffect, useMemo } from 'react';
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
  Edit3,
  Archive,
} from 'lucide-react';
import { RecipeSchema } from '../../types/recipe';
import { ExportCurationOptions, CuratedCookbook } from '../../types/export';
import { curateRecipes, flattenCuratedRecipes } from '../../utils/exportCuration';
import { ExportCurationToolbar } from './ExportCurationToolbar';
import { CsvPreview } from './CsvPreview';
import { JsonLdPreview } from './JsonLdPreview';
import { RawJsonPreview } from './RawJsonPreview';
import { MarkdownPreview } from './MarkdownPreview';
import { CooklangPreview } from './CooklangPreview';
import { GoogleDocsPreview } from './GoogleDocsPreview';
import { BinaryExportPreview } from './BinaryExportPreview';
import { ExportFilenameModal } from './ExportFilenameModal';
import { Button } from '../common/Button';
import { Badge } from '../common/Badge';
import { downloadPlanToEatCsv } from '../../utils/exportPlanToEat';
import { downloadJsonLd } from '../../utils/exportJsonLd';
import { downloadRawJson } from '../../utils/exportJson';
import { downloadMarkdown } from '../../utils/exportMarkdown';
import { downloadCooklang } from '../../utils/exportCooklang';
import { downloadGoogleDocsHtml } from '../../utils/exportGoogleDocs';
import { downloadDocx, downloadPdf, downloadPaprika, downloadProjectArchive } from '../../utils/exportBinary';

export interface ExportDialogProps {
  recipes: RecipeSchema[];
  sessionId?: string;
  isOpen?: boolean;
  onClose?: () => void;
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
  | 'pdf'
  | 'project';

export const getExtensionForTab = (tab: ExportTab): string => {
  switch (tab) {
    case 'csv': return '.csv';
    case 'jsonld': return '.json';
    case 'raw': return '.json';
    case 'markdown': return '.md';
    case 'cooklang': return '.cook';
    case 'paprika': return '.paprikarecipes';
    case 'googledocs': return '.html';
    case 'docx': return '.docx';
    case 'pdf': return '.pdf';
    case 'project': return '.recipeboxer';
  }
};

export const getSuggestedStem = (recipeList: RecipeSchema[], sid?: string): string => {
  const base =
    recipeList.length === 1 && recipeList[0].title
      ? recipeList[0].title
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, '_')
          .replace(/^_+|_+$/g, '')
      : 'recipeboxer_cookbook';
  const suffix = sid ? `_${sid.slice(-6)}` : '';
  return `${base || 'recipeboxer_export'}${suffix}`;
};

export const ExportDialog: React.FC<ExportDialogProps> = ({ recipes, sessionId }) => {
  const [activeTab, setActiveTab] = useState<ExportTab>('csv');
  const [customFilename, setCustomFilename] = useState<string>(() => {
    const stem = getSuggestedStem(recipes || [], sessionId);
    return `${stem}.csv`;
  });
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);

  // Curation and grouping state
  const [curationOptions, setCurationOptions] = useState<ExportCurationOptions>({
    recipe_order: (recipes || []).map((r) => r.recipe_id),
    grouping_preset: 'none',
    include_section_dividers: true,
    include_toc: true,
    toc_title: 'Table of Contents',
  });

  // Re-sync recipe IDs when recipes prop changes
  useEffect(() => {
    if (recipes && recipes.length > 0) {
      setCurationOptions((prev) => ({
        ...prev,
        recipe_order: prev.recipe_order && prev.recipe_order.length > 0
          ? prev.recipe_order
          : recipes.map((r) => r.recipe_id),
      }));
      const stem = getSuggestedStem(recipes, sessionId);
      const ext = getExtensionForTab(activeTab);
      setCustomFilename(`${stem}${ext}`);
    }
  }, [recipes, sessionId]);

  // Compute curated cookbook and flattened curated recipes
  const curatedCookbook: CuratedCookbook = useMemo(() => {
    return curateRecipes(recipes || [], curationOptions);
  }, [recipes, curationOptions]);

  const curatedRecipes: RecipeSchema[] = useMemo(() => {
    return flattenCuratedRecipes(curatedCookbook);
  }, [curatedCookbook]);

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

  const handleTabChange = (newTab: ExportTab) => {
    setActiveTab(newTab);
    const currentDot = customFilename.lastIndexOf('.');
    const stem = currentDot > 0 ? customFilename.substring(0, currentDot) : customFilename;
    const newExt = getExtensionForTab(newTab);
    setCustomFilename(`${stem || getSuggestedStem(recipes, sessionId)}${newExt}`);
  };

  const handleDownload = (filenameToUse?: string) => {
    const targetFilename = (filenameToUse || customFilename).trim() || `recipeboxer_export${getExtensionForTab(activeTab)}`;
    switch (activeTab) {
      case 'csv':
        downloadPlanToEatCsv(curatedRecipes, targetFilename);
        break;
      case 'jsonld':
        downloadJsonLd(curatedRecipes, targetFilename);
        break;
      case 'raw':
        downloadRawJson(curatedRecipes, targetFilename);
        break;
      case 'markdown':
        downloadMarkdown(curatedRecipes, targetFilename, true, curatedCookbook);
        break;
      case 'cooklang':
        downloadCooklang(curatedRecipes, targetFilename);
        break;
      case 'googledocs':
        downloadGoogleDocsHtml(curatedRecipes, targetFilename, curatedCookbook);
        break;
      case 'paprika':
        downloadPaprika(sessionId, targetFilename, curationOptions);
        break;
      case 'docx':
        downloadDocx(sessionId, targetFilename, curationOptions);
        break;
      case 'pdf':
        downloadPdf(sessionId, targetFilename, curationOptions);
        break;
      case 'project':
        downloadProjectArchive(sessionId, targetFilename);
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
      case 'project': return '.recipeboxer Project Archive';
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

        <div className="flex items-center gap-3">
          <Button
            variant="success"
            size="md"
            onClick={() => handleDownload()}
            leftIcon={<Download className="w-4 h-4" />}
          >
            Download {getActiveTabTitle()}
          </Button>
        </div>
      </div>

      {/* Customizable Export Filename Bar */}
      <div className="p-3.5 rounded-xl bg-slate-50 dark:bg-slate-850/60 border border-slate-200 dark:border-slate-800 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-700 dark:text-slate-300">
          <label htmlFor="export-filename-field" className="shrink-0 flex items-center gap-1.5">
            <Edit3 className="w-3.5 h-3.5 text-emerald-500" />
            <span>Export Filename:</span>
          </label>
          <input
            id="export-filename-field"
            type="text"
            value={customFilename}
            onChange={(e) => setCustomFilename(e.target.value)}
            className="px-3 py-1.5 text-xs font-mono rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-850 text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-emerald-500 min-w-[260px]"
            placeholder="recipeboxer_export.csv"
          />
        </div>

        <button
          type="button"
          onClick={() => setIsModalOpen(true)}
          className="text-xs text-emerald-600 dark:text-emerald-400 hover:underline cursor-pointer flex items-center gap-1"
        >
          <span>More Options</span>
        </button>
      </div>

      {/* Curation & Grouping Toolbar (R3) */}
      <ExportCurationToolbar
        recipes={recipes}
        curatedCookbook={curatedCookbook}
        curationOptions={curationOptions}
        onChangeOptions={setCurationOptions}
      />

      {/* Group 1: Primary Formats (R0) */}
      <div className="flex flex-col gap-2">
        <span className="text-[11px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
          Primary Formats (RFC 4180 & Schema.org)
        </span>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {/* Plan To Eat CSV */}
          <button
            type="button"
            onClick={() => handleTabChange('csv')}
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
            onClick={() => handleTabChange('jsonld')}
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
            onClick={() => handleTabChange('raw')}
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
            onClick={() => handleTabChange('markdown')}
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
            onClick={() => handleTabChange('cooklang')}
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
            onClick={() => handleTabChange('paprika')}
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
            disabled={true}
            title="Support In Progress"
            className="p-3.5 rounded-xl border text-left flex flex-col gap-1 transition-all opacity-50 cursor-not-allowed border-slate-200 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-850/50 text-slate-700 dark:text-slate-300"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 font-semibold text-xs text-slate-600 dark:text-slate-400">
                <BookOpen className="w-4 h-4" />
                <span>Google Docs Archival</span>
              </div>
              <Badge variant="warning" size="sm">Coming Soon</Badge>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              Direct OAuth publishing & HTML archive.
            </p>
          </button>

          {/* Offline DOCX */}
          <button
            type="button"
            onClick={() => handleTabChange('docx')}
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
            onClick={() => handleTabChange('pdf')}
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

      {/* Group 4: Portable Project State (.recipeboxer Archive) */}
      <div className="flex flex-col gap-2">
        <span className="text-[11px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
          Portable Project State & Backup (Phase 9)
        </span>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {/* Project Archive */}
          <button
            type="button"
            onClick={() => handleTabChange('project')}
            className={`p-3.5 rounded-xl border text-left flex flex-col gap-1 transition-all cursor-pointer ${
              activeTab === 'project'
                ? 'border-emerald-500 bg-emerald-50/50 dark:bg-emerald-950/30 ring-2 ring-emerald-500/20'
                : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-slate-50/50 dark:bg-slate-850/50'
            }`}
          >
            <div className="flex items-center gap-2 font-semibold text-xs text-emerald-700 dark:text-emerald-400">
              <Archive className="w-4 h-4" />
              <span>.recipeboxer Project Archive</span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              Complete portable backup (scans, fingerprints, proposals, recipes, marginalia).
            </p>
          </button>
        </div>
      </div>

      {/* Format Content Preview */}
      <div className="pt-2">
        {activeTab === 'csv' && <CsvPreview recipes={curatedRecipes} />}
        {activeTab === 'jsonld' && <JsonLdPreview recipes={curatedRecipes} />}
        {activeTab === 'raw' && <RawJsonPreview recipes={curatedRecipes} />}
        {activeTab === 'markdown' && (
          <MarkdownPreview recipes={curatedRecipes} curation={curatedCookbook} />
        )}
        {activeTab === 'cooklang' && <CooklangPreview recipes={curatedRecipes} />}
        {activeTab === 'googledocs' && (
          <GoogleDocsPreview
            recipes={curatedRecipes}
            sessionId={sessionId}
            customFilename={customFilename}
            curation={curatedCookbook}
            curationOptions={curationOptions}
          />
        )}
        {activeTab === 'paprika' && (
          <BinaryExportPreview
            format="paprika"
            recipes={curatedRecipes}
            onDownload={() => handleDownload()}
          />
        )}
        {activeTab === 'docx' && (
          <BinaryExportPreview
            format="docx"
            recipes={curatedRecipes}
            onDownload={() => handleDownload()}
          />
        )}
        {activeTab === 'pdf' && (
          <BinaryExportPreview
            format="pdf"
            recipes={curatedRecipes}
            onDownload={() => handleDownload()}
          />
        )}
        {activeTab === 'project' && (
          <BinaryExportPreview
            format="project"
            recipes={curatedRecipes}
            onDownload={() => handleDownload()}
          />
        )}
      </div>

      {/* Custom Filename Modal */}
      <ExportFilenameModal
        isOpen={isModalOpen}
        filename={customFilename}
        formatName={getActiveTabTitle()}
        onSave={(newFn) => setCustomFilename(newFn)}
        onClose={() => setIsModalOpen(false)}
        onDownload={(fn) => handleDownload(fn)}
      />
    </div>
  );
};
