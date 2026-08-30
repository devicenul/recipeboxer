import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { ExportDialog } from '../src/components/export/ExportDialog';
import { GoogleDocsPreview } from '../src/components/export/GoogleDocsPreview';
import { ExportFilenameModal } from '../src/components/export/ExportFilenameModal';
import { RecipeSchema } from '../src/types/recipe';

describe('Milestone 4 Empirical Challenger Adversarial Tests (Frontend)', () => {
  const adversarialRecipes: RecipeSchema[] = [
    {
      recipe_id: 'rec_adv_001',
      title: 'Grandma\'s "Special" Apple & Cinnamon Pie <Heirloom>',
      description: 'Vintage recipe with caramel sauce and roasted pecans.',
      yield_servings: '8 servings',
      prep_time: '25 mins',
      cook_time: '50 mins',
      total_time: '1 hr 15 mins',
      ingredients: [
        '6 cups peeled & sliced Granny Smith apples',
        '3/4 cup sugar',
        '1 tbsp ground cinnamon',
        '2 crusts (top & bottom)',
      ],
      directions: [
        '1. Toss sliced apples with sugar and cinnamon in a bowl.',
        '2. Fill pie plate with bottom crust and add apples.',
        '3. Cover with top crust, crimp edges, and bake at 375°F for 50 minutes.',
      ],
      tags: ['Dessert, Baking', 'Mom\'s "Favorite"', 'Autumn & Winter', '<Holiday>'],
      source_notes: 'From vintage family recipe box (1954)',
      card_ids: ['card_tart_01', 'card_tart_02'],
    },
  ];

  describe('Area 1: Filename Customization & Tab Switching Stem Synchronization', () => {
    it('preserves user custom stem while updating file extensions across all 9 tabs', () => {
      render(<ExportDialog recipes={adversarialRecipes} sessionId="sess_adv_test_999" />);

      const filenameInput = screen.getByLabelText(/Export Filename:/i) as HTMLInputElement;
      expect(filenameInput).toBeInTheDocument();

      // Enter custom stem
      fireEvent.change(filenameInput, { target: { value: 'grandma_heirloom_deck_2026.csv' } });
      expect(filenameInput.value).toBe('grandma_heirloom_deck_2026.csv');

      const tabExpectations = [
        { name: /Schema\.org JSON-LD/i, expectedExt: '.json' },
        { name: /Standard Raw JSON/i, expectedExt: '.json' },
        { name: /Obsidian Markdown/i, expectedExt: '.md' },
        { name: /Cooklang \(\.cook\)/i, expectedExt: '.cook' },
        { name: /Paprika \(\.paprikarecipes\)/i, expectedExt: '.paprikarecipes' },
        { name: /Google Docs Archival/i, expectedExt: '.html' },
        { name: /Heirloom DOCX/i, expectedExt: '.docx' },
        { name: /Printable PDF/i, expectedExt: '.pdf' },
        { name: /Plan To Eat CSV/i, expectedExt: '.csv' },
      ];

      for (const { name, expectedExt } of tabExpectations) {
        const tabBtn = screen.getByRole('button', { name });
        fireEvent.click(tabBtn);
        expect(filenameInput.value).toBe(`grandma_heirloom_deck_2026${expectedExt}`);
      }
    });

    it('handles empty custom filename by falling back to auto-generated suggested stem', () => {
      render(<ExportDialog recipes={adversarialRecipes} sessionId="sess_fallback_123" />);

      const filenameInput = screen.getByLabelText(/Export Filename:/i) as HTMLInputElement;

      // Clear input completely
      fireEvent.change(filenameInput, { target: { value: '' } });
      expect(filenameInput.value).toBe('');

      // Switch tab -> should fall back to suggested stem
      const docxTab = screen.getByRole('button', { name: /Heirloom DOCX/i });
      fireEvent.click(docxTab);

      expect(filenameInput.value).toContain('.docx');
      expect(filenameInput.value.length).toBeGreaterThan(5);
    });

    it('modal supports Enter key trigger for immediate download action', () => {
      const handleDownloadMock = vi.fn();
      const handleSaveMock = vi.fn();
      const handleCloseMock = vi.fn();

      render(
        <ExportFilenameModal
          isOpen={true}
          filename="holiday_recipes.pdf"
          formatName="Printable PDF"
          onSave={handleSaveMock}
          onClose={handleCloseMock}
          onDownload={handleDownloadMock}
        />
      );

      const modalInput = screen.getByLabelText(/Filename for Printable PDF/i) as HTMLInputElement;
      fireEvent.change(modalInput, { target: { value: 'custom_enter_download.pdf' } });

      // Press Enter key
      fireEvent.keyDown(modalInput, { key: 'Enter', code: 'Enter' });

      expect(handleSaveMock).toHaveBeenCalledWith('custom_enter_download.pdf');
      expect(handleDownloadMock).toHaveBeenCalledWith('custom_enter_download.pdf');
      expect(handleCloseMock).toHaveBeenCalled();
    });
  });

  describe('Area 2: Google Docs OAuth Error Resilience', () => {
    it('displays error message when token request fails with 401 unauthorized', async () => {
      const originalFetch = global.fetch;
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: () => Promise.resolve({ detail: 'Google OAuth authorization failed: 401 Unauthorized' }),
      } as Response);

      render(<GoogleDocsPreview recipes={adversarialRecipes} sessionId="sess_oauth_fail" />);

      // Click 1-click GIS export button
      const exportBtn = screen.getByRole('button', { name: /Export directly to Google Docs/i });
      fireEvent.click(exportBtn);

      // Verify error banner is rendered
      await waitFor(() => {
        expect(screen.getByText(/Google Docs export failed: Google OAuth authorization failed/i)).toBeInTheDocument();
      });

      global.fetch = originalFetch;
    });

    it('displays error message when network exception or 502 bad gateway occurs', async () => {
      const originalFetch = global.fetch;
      global.fetch = vi.fn().mockRejectedValue(new Error('Network connection timeout to Google Drive'));

      render(<GoogleDocsPreview recipes={adversarialRecipes} sessionId="sess_timeout" />);

      // Click 1-click GIS export button
      const exportBtn = screen.getByRole('button', { name: /Export directly to Google Docs/i });
      fireEvent.click(exportBtn);

      await waitFor(() => {
        expect(screen.getByText(/Network connection timeout to Google Drive/i)).toBeInTheDocument();
      });

      global.fetch = originalFetch;
    });
  });
});
