import React, { useState } from 'react';
import { Download, FileSpreadsheet, Code2, FileJson, ChefHat } from 'lucide-react';
import { RecipeSchema } from '../../types/recipe';
import { CsvPreview } from './CsvPreview';
import { JsonLdPreview } from './JsonLdPreview';
import { RawJsonPreview } from './RawJsonPreview';
import { Button } from '../common/Button';
import { downloadPlanToEatCsv } from '../../utils/exportPlanToEat';
import { downloadJsonLd } from '../../utils/exportJsonLd';
import { downloadRawJson } from '../../utils/exportJson';

export interface ExportDialogProps {
  recipes: RecipeSchema[];
  sessionId?: string;
}

export type ExportTab = 'csv' | 'jsonld' | 'raw';

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
    if (activeTab === 'csv') {
      downloadPlanToEatCsv(recipes, `recipeboxer_export${filenameSuffix}.csv`);
    } else if (activeTab === 'jsonld') {
      downloadJsonLd(recipes, `recipeboxer_json_ld${filenameSuffix}.json`);
    } else {
      downloadRawJson(recipes, `recipeboxer_recipes${filenameSuffix}.json`);
    }
  };

  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-6 shadow-xs flex flex-col gap-6">
      {/* Top Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-slate-200 dark:border-slate-800">
        <div>
          <h2 className="text-base font-bold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Download className="w-5 h-5 text-emerald-500" />
            <span>Multi-Format Recipe Export</span>
          </h2>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Export {recipes.length} digitized recipes into your favorite recipe manager.
          </p>
        </div>

        <Button
          variant="success"
          size="md"
          onClick={handleDownload}
          leftIcon={<Download className="w-4 h-4" />}
        >
          Download{' '}
          {activeTab === 'csv'
            ? 'Plan To Eat CSV'
            : activeTab === 'jsonld'
            ? 'Schema.org JSON-LD'
            : 'Raw Recipe JSON'}
        </Button>
      </div>

      {/* Format Selector Tabs */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {/* Tab 1: Plan To Eat CSV */}
        <button
          type="button"
          onClick={() => setActiveTab('csv')}
          className={`p-4 rounded-xl border text-left flex flex-col gap-1.5 transition-all cursor-pointer ${
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
            RFC 4180 compliant 9-column CSV import for Plan To Eat, Paprika, and Cooklang.
          </p>
        </button>

        {/* Tab 2: Schema.org JSON-LD */}
        <button
          type="button"
          onClick={() => setActiveTab('jsonld')}
          className={`p-4 rounded-xl border text-left flex flex-col gap-1.5 transition-all cursor-pointer ${
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
            Web standard microdata compatible with Mealie, Google Search, and Recipe apps.
          </p>
        </button>

        {/* Tab 3: Raw JSON */}
        <button
          type="button"
          onClick={() => setActiveTab('raw')}
          className={`p-4 rounded-xl border text-left flex flex-col gap-1.5 transition-all cursor-pointer ${
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
            Lossless structured RecipeSchema JSON array for developer backup and integration.
          </p>
        </button>
      </div>

      {/* Format Content Preview */}
      <div className="pt-2">
        {activeTab === 'csv' && <CsvPreview recipes={recipes} />}
        {activeTab === 'jsonld' && <JsonLdPreview recipes={recipes} />}
        {activeTab === 'raw' && <RawJsonPreview recipes={recipes} />}
      </div>
    </div>
  );
};
