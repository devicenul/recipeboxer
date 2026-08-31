import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { ExportDialog } from '../src/components/export/ExportDialog';
import { SessionHistoryTable } from '../src/components/ingestion/SessionHistoryTable';
import { api } from '../src/utils/api';
import { RecipeSchema } from '../src/types/recipe';
import { SessionSummary, PipelineState } from '../src/types/pipeline';

describe('Milestone 1: Portable Project State Persistence (.recipeboxer)', () => {
  const sampleRecipes: RecipeSchema[] = [
    {
      recipe_id: 'rec_001',
      title: 'Grandma\'s Midwestern Apple Crisp',
      description: 'Vintage 1948 family recipe card.',
      yield_servings: '6 servings',
      prep_time: '20 mins',
      cook_time: '35 mins',
      total_time: '55 mins',
      ingredients: ['5 apples', '1 cup oats', '1/2 cup brown sugar', '1/3 cup butter'],
      directions: ['1. Preheat oven to 375F.', '2. Spread topping over apples.', '3. Bake for 35 mins.'],
      tags: ['Dessert', 'Heirloom', 'Grandma'],
      source_notes: '1948 Handwritten Card',
      card_ids: ['card_front_001.jpg', 'card_back_001.jpg'],
    },
  ];

  const sampleSessions: SessionSummary[] = [
    {
      session_id: 'sess_1001',
      status: PipelineState.COMPLETED,
      card_count: 2,
      recipe_count: 1,
      created_at: '2026-08-31T04:00:00Z',
    },
    {
      session_id: 'sess_1002',
      status: PipelineState.HITL_PENDING,
      card_count: 4,
      recipe_count: 0,
      created_at: '2026-08-31T04:10:00Z',
    },
  ];

  describe('ExportDialog - .recipeboxer Archive Tab', () => {
    it('renders the .recipeboxer Project Archive tab and preview', () => {
      render(<ExportDialog recipes={sampleRecipes} sessionId="sess_test_123" />);

      // Find and click .recipeboxer tab
      const projectTabBtn = screen.getByText('.recipeboxer Project Archive');
      expect(projectTabBtn).toBeInTheDocument();
      fireEvent.click(projectTabBtn);

      // Verify filename input update
      const filenameInput = screen.getByLabelText(/Export Filename:/i) as HTMLInputElement;
      expect(filenameInput.value).toContain('.recipeboxer');

      // Verify preview features
      expect(screen.getByText('Portable Project State Container')).toBeInTheDocument();
      expect(screen.getByText(/Complete lossless session snapshot/i)).toBeInTheDocument();
    });
  });

  describe('SessionHistoryTable - Import Archive Button', () => {
    it('renders the Import Archive (.recipeboxer) button and handles file selection', async () => {
      const mockOnImport = vi.fn();
      const mockOnRefresh = vi.fn();

      render(
        <SessionHistoryTable
          sessions={sampleSessions}
          onResumeSession={vi.fn()}
          onViewRecipes={vi.fn()}
          onDeleteSession={vi.fn()}
          onRefresh={mockOnRefresh}
          onImportArchive={mockOnImport}
        />
      );

      const importBtn = screen.getByTitle('Import Portable .recipeboxer Archive');
      expect(importBtn).toBeInTheDocument();

      // Find hidden file input
      const fileInput = document.getElementById('history-archive-import-input') as HTMLInputElement;
      expect(fileInput).toBeInTheDocument();
      expect(fileInput.accept).toContain('.recipeboxer');

      // Simulate file upload
      const dummyFile = new File(['dummy archive content'], 'project.recipeboxer', {
        type: 'application/x-recipeboxer',
      });
      fireEvent.change(fileInput, { target: { files: [dummyFile] } });

      await waitFor(() => {
        expect(mockOnImport).toHaveBeenCalledWith(dummyFile);
      });
    });
  });

  describe('API Client - Archive Persistence Methods', () => {
    let originalFetch: typeof global.fetch;

    beforeEach(() => {
      originalFetch = global.fetch;
    });

    afterEach(() => {
      global.fetch = originalFetch;
    });

    it('api.importProjectArchive sends multipart form data and returns SessionDetail', async () => {
      const mockSessionDetail = {
        session_id: 'sess_restored_api',
        status: 'COMPLETED',
        mode: 'parallel',
        card_count: 2,
        recipe_count: 1,
        cards: [],
        proposals: [],
        recipes: sampleRecipes,
        created_at: '2026-08-31T04:00:00Z',
        updated_at: '2026-08-31T04:00:00Z',
      };

      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => mockSessionDetail,
      });

      const dummyFile = new File(['dummy archive bytes'], 'archive.recipeboxer', {
        type: 'application/x-recipeboxer',
      });
      const result = await api.importProjectArchive(dummyFile, true, 'sess_override_id');

      expect(global.fetch).toHaveBeenCalledWith('/api/project/import', expect.objectContaining({
        method: 'POST',
      }));
      expect(result.session_id).toBe('sess_restored_api');
      expect(result.recipe_count).toBe(1);
    });

    it('api.mergeProjectArchive sends base and incoming files and returns Blob', async () => {
      const dummyBlob = new Blob(['merged archive bytes'], { type: 'application/x-recipeboxer' });

      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        blob: async () => dummyBlob,
      });

      const baseFile = new File(['base zip bytes'], 'base.recipeboxer');
      const incomingFile = new File(['incoming zip bytes'], 'incoming.recipeboxer');

      const result = await api.mergeProjectArchive(
        baseFile,
        { file: incomingFile },
        'prefer_incoming',
        'merged_output.recipeboxer'
      );

      expect(global.fetch).toHaveBeenCalledWith('/api/project/merge', expect.objectContaining({
        method: 'POST',
      }));
      expect(result.size).toBe(dummyBlob.size);
    });
  });
});
