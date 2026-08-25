import React, { useState } from 'react';
import { Tag, Plus, X } from 'lucide-react';

export interface TagManagerProps {
  tags: string[];
  onAddTag: (tag: string) => void;
  onRemoveTag: (tag: string) => void;
  disabled?: boolean;
}

export const TagManager: React.FC<TagManagerProps> = ({
  tags = [],
  onAddTag,
  onRemoveTag,
  disabled = false,
}) => {
  const [newTagInput, setNewTagInput] = useState('');

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      if (newTagInput.trim()) {
        onAddTag(newTagInput.trim());
        setNewTagInput('');
      }
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <label className="text-xs font-semibold text-slate-800 dark:text-slate-200 flex items-center gap-1.5">
        <Tag className="w-3.5 h-3.5 text-blue-500" />
        Tags &amp; Categories ({tags.length})
      </label>

      <div className="flex flex-wrap items-center gap-1.5 p-2 bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg min-h-[42px]">
        {tags.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300 border border-blue-200 dark:border-blue-800 shadow-xs"
          >
            <span>{tag}</span>
            {!disabled && (
              <button
                type="button"
                onClick={() => onRemoveTag(tag)}
                className="hover:text-rose-600 dark:hover:text-rose-400 p-0.5 cursor-pointer"
                title={`Remove tag ${tag}`}
              >
                <X className="w-3 h-3" />
              </button>
            )}
          </span>
        ))}

        {!disabled && (
          <div className="flex items-center gap-1 min-w-[120px] flex-1">
            <input
              type="text"
              placeholder="+ Add tag (press Enter)"
              value={newTagInput}
              onChange={(e) => setNewTagInput(e.target.value)}
              onKeyDown={handleKeyDown}
              className="w-full text-xs bg-transparent border-none focus:outline-none text-slate-900 dark:text-slate-100 placeholder-slate-400 px-1 py-0.5"
            />
          </div>
        )}
      </div>
    </div>
  );
};
