import React, { useEffect, useState } from 'react';
import { Search, Folder, Image as ImageIcon, Check, RefreshCw, AlertCircle } from 'lucide-react';
import { Modal } from '../common/Modal';
import { Button } from '../common/Button';
import { Badge } from '../common/Badge';
import { SourceContainer } from '../../types/sources';
import { api } from '../../utils/api';

export interface AlbumPickerModalProps {
  isOpen: boolean;
  onClose: () => void;
  providerId: string;
  providerName: string;
  selectedContainerId?: string | null;
  onSelectContainer: (container: SourceContainer) => void;
}

export const AlbumPickerModal: React.FC<AlbumPickerModalProps> = ({
  isOpen,
  onClose,
  providerId,
  providerName,
  selectedContainerId,
  onSelectContainer,
}) => {
  const [containers, setContainers] = useState<SourceContainer[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [activeSelection, setActiveSelection] = useState<SourceContainer | null>(null);

  const fetchContainers = async () => {
    if (!providerId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.listSourceContainers(providerId, { pageSize: 50 });
      setContainers(res.containers || []);
      if (selectedContainerId) {
        const found = res.containers?.find((c) => c.id === selectedContainerId);
        if (found) setActiveSelection(found);
      }
    } catch (err: any) {
      setError(err.message || `Failed to fetch containers from ${providerName}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      setActiveSelection(null);
      fetchContainers();
      setSearchQuery('');
    }
  }, [isOpen, providerId]);

  const filteredContainers = containers.filter((c) =>
    c.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleConfirm = () => {
    if (activeSelection) {
      onSelectContainer(activeSelection);
      onClose();
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`Select ${providerName} Album or Folder`}
      maxWidth="2xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!activeSelection || loading}
            onClick={handleConfirm}
            leftIcon={<Check className="w-4 h-4" />}
          >
            Select Container
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {/* Search & Refresh Header */}
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-3 text-slate-400" />
            <input
              type="text"
              placeholder={`Search ${providerName} albums or folders...`}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-4 py-2 text-sm bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={fetchContainers}
            isLoading={loading}
            leftIcon={<RefreshCw className="w-4 h-4" />}
          >
            Refresh
          </Button>
        </div>

        {/* Error Alert */}
        {error && (
          <div className="flex items-center gap-2 p-3 bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 rounded-lg text-xs text-rose-700 dark:text-rose-300">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Containers Grid / List */}
        {loading && containers.length === 0 ? (
          <div className="py-12 flex flex-col items-center justify-center gap-3 text-slate-500">
            <RefreshCw className="w-6 h-6 animate-spin text-blue-600" />
            <p className="text-sm">Loading containers from {providerName}...</p>
          </div>
        ) : filteredContainers.length === 0 ? (
          <div className="py-12 text-center text-slate-500 dark:text-slate-400">
            <Folder className="w-10 h-10 mx-auto mb-2 opacity-40" />
            <p className="text-sm font-medium">No albums or folders found</p>
            <p className="text-xs text-slate-400 mt-1">
              {searchQuery ? 'Try matching a different search term.' : 'No remote containers found in this account.'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-h-[50vh] overflow-y-auto pr-1">
            {filteredContainers.map((container) => {
              const isSelected = activeSelection?.id === container.id;
              return (
                <div
                  key={container.id}
                  onClick={() => setActiveSelection(container)}
                  onDoubleClick={() => {
                    setActiveSelection(container);
                    onSelectContainer(container);
                    onClose();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setActiveSelection(container);
                      onSelectContainer(container);
                      onClose();
                    }
                  }}
                  className={`flex items-center gap-3 p-3 rounded-xl border transition-all cursor-pointer ${
                    isSelected
                      ? 'border-blue-600 bg-blue-50/50 dark:bg-blue-950/30 ring-2 ring-blue-500/20'
                      : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 bg-white dark:bg-slate-900'
                  }`}
                  role="button"
                  tabIndex={0}
                  aria-pressed={isSelected}
                >
                  {/* Thumbnail Cover */}
                  <div className="w-12 h-12 rounded-lg bg-slate-100 dark:bg-slate-800 flex items-center justify-center shrink-0 overflow-hidden border border-slate-200 dark:border-slate-700">
                    {container.cover_image_url ? (
                      <img
                        src={container.cover_image_url}
                        alt={container.name}
                        className="w-full h-full object-cover"
                      />
                    ) : (
                      <Folder className="w-6 h-6 text-slate-400" />
                    )}
                  </div>

                  {/* Details */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-slate-900 dark:text-slate-100 truncate">
                      {container.name}
                    </p>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-xs text-slate-500 dark:text-slate-400 capitalize">
                        {container.container_type || 'album'}
                      </span>
                      {container.item_count !== undefined && container.item_count !== null && (
                        <Badge variant="neutral" size="sm">
                          {container.item_count} items
                        </Badge>
                      )}
                    </div>
                  </div>

                  {/* Selection Indicator */}
                  <div
                    className={`w-5 h-5 rounded-full flex items-center justify-center border transition-all ${
                      isSelected
                        ? 'border-blue-600 bg-blue-600 text-white'
                        : 'border-slate-300 dark:border-slate-600'
                    }`}
                  >
                    {isSelected && <Check className="w-3.5 h-3.5" />}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </Modal>
  );
};
