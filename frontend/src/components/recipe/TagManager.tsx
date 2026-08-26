import React, { useState, useRef, useEffect } from 'react';
import { Tag, Plus, X, Check, Edit2 } from 'lucide-react';

export interface TagManagerProps {
  tags: string[];
  onAddTag: (tag: string) => void;
  onRemoveTag: (tag: string) => void;
  onEditTag?: (oldTag: string, newTag: string) => void;
  disabled?: boolean;
}

export const TagManager: React.FC<TagManagerProps> = ({
  tags = [],
  onAddTag,
  onRemoveTag,
  onEditTag,
  disabled = false,
}) => {
  const [newTagInput, setNewTagInput] = useState('');
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editingValue, setEditingValue] = useState<string>('');
  const editInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingIndex !== null && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [editingIndex]);

  const commitAdd = (value: string) => {
    const raw = value.trim();
    if (!raw) return;

    // Support comma or semicolon delimited multi-tag entry
    const parts = raw.split(/[,;]+/).map((p) => p.trim()).filter(Boolean);
    parts.forEach((part) => {
      onAddTag(part);
    });
    setNewTagInput('');
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',' || e.key === ';') {
      e.preventDefault();
      commitAdd(newTagInput);
    }
  };

  const handleBlur = () => {
    if (newTagInput.trim()) {
      commitAdd(newTagInput);
    }
  };

  const startEditing = (idx: number, currentTag: string) => {
    if (disabled) return;
    setEditingIndex(idx);
    setEditingValue(currentTag);
  };

  const commitEdit = (idx: number, oldTag: string) => {
    const cleanNew = editingValue.trim();
    setEditingIndex(null);
    if (!cleanNew) {
      onRemoveTag(oldTag);
      return;
    }
    if (cleanNew !== oldTag) {
      if (onEditTag) {
        onEditTag(oldTag, cleanNew);
      } else {
        onRemoveTag(oldTag);
        onAddTag(cleanNew);
      }
    }
  };

  const handleEditKeyDown = (
    e: React.KeyboardEvent<HTMLInputElement>,
    idx: number,
    oldTag: string
  ) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      commitEdit(idx, oldTag);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setEditingIndex(null);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <label className="text-xs font-semibold text-slate-800 dark:text-slate-200 flex items-center gap-1.5">
        <Tag className="w-3.5 h-3.5 text-blue-500" />
        Tags &amp; Categories ({tags.length})
      </label>

      <div className="flex flex-wrap items-center gap-1.5 p-2 bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg min-h-[42px]">
        {tags.map((tag, idx) => {
          const isEditing = editingIndex === idx;

          if (isEditing) {
            return (
              <span
                key={`editing-${idx}`}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium bg-blue-50 text-blue-900 dark:bg-blue-900/60 dark:text-blue-100 border border-blue-400 dark:border-blue-500 shadow-xs"
              >
                <input
                  ref={editInputRef}
                  type="text"
                  value={editingValue}
                  onChange={(e) => setEditingValue(e.target.value)}
                  onKeyDown={(e) => handleEditKeyDown(e, idx, tag)}
                  onBlur={() => commitEdit(idx, tag)}
                  className="w-24 px-1 py-0.5 text-xs bg-white dark:bg-slate-900 border border-blue-500 rounded text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
                <button
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => commitEdit(idx, tag)}
                  className="hover:text-emerald-600 dark:hover:text-emerald-400 p-0.5 cursor-pointer"
                  title="Confirm edit"
                >
                  <Check className="w-3 h-3" />
                </button>
              </span>
            );
          }

          return (
            <span
              key={`${tag}-${idx}`}
              className="group inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300 border border-blue-200 dark:border-blue-800 shadow-xs"
            >
              <span
                onClick={() => startEditing(idx, tag)}
                className={disabled ? '' : 'cursor-pointer hover:underline'}
                title={disabled ? undefined : 'Click to edit tag'}
              >
                {tag}
              </span>
              {!disabled && (
                <>
                  <button
                    type="button"
                    onClick={() => startEditing(idx, tag)}
                    className="opacity-0 group-hover:opacity-100 text-blue-600 dark:text-blue-400 hover:text-blue-900 dark:hover:text-blue-200 p-0.5 cursor-pointer transition-opacity"
                    title={`Edit tag ${tag}`}
                  >
                    <Edit2 className="w-2.5 h-2.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => onRemoveTag(tag)}
                    className="hover:text-rose-600 dark:hover:text-rose-400 p-0.5 cursor-pointer ml-0.5"
                    title={`Remove tag ${tag}`}
                  >
                    <X className="w-3 h-3" />
                  </button>
                </>
              )}
            </span>
          );
        })}

        {!disabled && (
          <div className="flex items-center gap-1 min-w-[140px] flex-1">
            <input
              type="text"
              placeholder="+ Add tag (press Enter)"
              value={newTagInput}
              onChange={(e) => setNewTagInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onBlur={handleBlur}
              className="w-full text-xs bg-transparent border-none focus:outline-none text-slate-900 dark:text-slate-100 placeholder-slate-400 px-1 py-0.5"
            />
            {newTagInput.trim() && (
              <button
                type="button"
                onClick={() => commitAdd(newTagInput)}
                className="inline-flex items-center gap-0.5 px-2 py-0.5 text-xs font-semibold rounded bg-blue-600 hover:bg-blue-700 text-white shadow-xs cursor-pointer"
                title="Add tag"
              >
                <Plus className="w-3 h-3" /> Add
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
