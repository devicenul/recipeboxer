import React, { useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { RecipeSchema } from '../../types/recipe';
import { generateMarkdown } from '../../utils/exportMarkdown';

export const MarkdownPreview: React.FC<{ recipes: RecipeSchema[] }> = ({ recipes }) => {
  const [copied, setCopied] = useState(false);
  const content = generateMarkdown(recipes);

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between text-xs text-slate-600 dark:text-slate-400">
        <span>Obsidian Markdown with YAML Frontmatter ({recipes.length} recipes)</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-purple-600 dark:text-purple-400 hover:underline cursor-pointer"
        >
          {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
          <span>{copied ? 'Copied!' : 'Copy to Clipboard'}</span>
        </button>
      </div>
      <pre className="p-4 bg-slate-900 text-slate-100 font-mono text-xs rounded-xl overflow-x-auto max-h-96 leading-relaxed border border-slate-800">
        {content}
      </pre>
    </div>
  );
};
