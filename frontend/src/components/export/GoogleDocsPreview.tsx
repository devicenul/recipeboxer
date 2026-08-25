import React, { useState } from 'react';
import { Copy, Check, ExternalLink } from 'lucide-react';
import { RecipeSchema } from '../../types/recipe';
import { generateGoogleDocsHtml } from '../../utils/exportGoogleDocs';

export const GoogleDocsPreview: React.FC<{ recipes: RecipeSchema[] }> = ({ recipes }) => {
  const [copied, setCopied] = useState(false);
  const htmlContent = generateGoogleDocsHtml(recipes);

  const handleCopy = () => {
    navigator.clipboard.writeText(htmlContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between text-xs text-slate-600 dark:text-slate-400">
        <span>Google Docs Interchange Archive ({recipes.length} recipes)</span>
        <div className="flex items-center gap-3">
          <button
            onClick={handleCopy}
            className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400 hover:underline cursor-pointer"
          >
            {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{copied ? 'Copied HTML' : 'Copy HTML'}</span>
          </button>
        </div>
      </div>
      <div className="border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden bg-white dark:bg-slate-900 max-h-96 overflow-y-auto p-4 shadow-inner">
        <div className="text-xs text-slate-500 dark:text-slate-400 pb-2 mb-3 border-b border-slate-100 dark:border-slate-800 flex items-center gap-1">
          <ExternalLink className="w-3 h-3" />
          <span>Interactive Rich Preview for Google Docs Import</span>
        </div>
        <div
          className="prose dark:prose-invert max-w-none text-xs"
          dangerouslySetInnerHTML={{ __html: htmlContent }}
        />
      </div>
    </div>
  );
};
