import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { ExportDialog } from '../src/components/export/ExportDialog';
import { generatePlanToEatCsv, escapeCsvField } from '../src/utils/exportPlanToEat';
import { generateJsonLd, recipeToSchemaOrg } from '../src/utils/exportJsonLd';
import { generateRawJson } from '../src/utils/exportJson';
import { generateMarkdown, recipeToMarkdown } from '../src/utils/exportMarkdown';
import { generateCooklang, recipeToCooklang } from '../src/utils/exportCooklang';
import { generateGoogleDocsHtml } from '../src/utils/exportGoogleDocs';
import { RecipeSchema } from '../src/types/recipe';

describe('R5: Multi-Format Recipe Export Subsystem', () => {
  const sampleRecipes: RecipeSchema[] = [
    {
      recipe_id: 'rec_001',
      title: 'Grandma\'s "Secret" Apple Pie',
      description: 'Holiday pie with cinnamon, nutmeg & butter.',
      yield_servings: '8 slices',
      prep_time: '20 mins',
      cook_time: '45 mins',
      total_time: '1 hr 5 mins',
      ingredients: ['6 cups sliced apples', '3/4 cup sugar', '1 tbsp cinnamon'],
      directions: ['1. Mix apples and spices.', '2. Fill crust and bake at 375F.'],
      tags: ['Dessert', 'Baking', 'Family Tradition'],
      source_notes: "Grandma's recipe box",
      card_ids: ['card_front_001', 'card_back_001'],
    },
  ];

  describe('Plan To Eat RFC 4180 CSV Serializer', () => {
    it('escapes quotes and special characters per RFC 4180 rules', () => {
      expect(escapeCsvField('Simple Text')).toBe('Simple Text');
      expect(escapeCsvField('Text with, comma')).toBe('"Text with, comma"');
      expect(escapeCsvField('Text with "quotes"')).toBe('"Text with ""quotes"""');
      expect(escapeCsvField('Line 1\nLine 2')).toBe('"Line 1\nLine 2"');
    });

    it('generates exact 9 columns with CRLF line terminators', () => {
      const csv = generatePlanToEatCsv(sampleRecipes);

      // Verify headers
      expect(csv).toContain('Title,Description,Ingredients,Directions,Prep Time,Total Time,Yield,Tags,Source');
      // Verify CRLF
      expect(csv).toContain('\r\n');
      // Verify title with escaped quotes
      expect(csv).toContain('"Grandma\'s ""Secret"" Apple Pie"');
      // Verify multiline ingredients
      expect(csv).toContain('"6 cups sliced apples\n3/4 cup sugar\n1 tbsp cinnamon"');
      // Verify auto-numbered directions
      expect(csv).toContain('"1. Mix apples and spices.\n2. Fill crust and bake at 375F."');
      // Verify tags comma separated
      expect(csv).toContain('"Dessert, Baking, Family Tradition"');
    });
  });

  describe('Schema.org JSON-LD Serializer', () => {
    it('generates compliant Schema.org Recipe structure with ISO 8601 durations and HowToStep steps', () => {
      const schema = recipeToSchemaOrg(sampleRecipes[0]);

      expect(schema['@context']).toBe('https://schema.org');
      expect(schema['@type']).toBe('Recipe');
      expect(schema.name).toBe('Grandma\'s "Secret" Apple Pie');
      expect(schema.prepTime).toBe('PT20M');
      expect(schema.cookTime).toBe('PT45M');
      expect(schema.totalTime).toBe('PT1H5M');
      expect(schema.recipeYield).toBe('8 slices');

      expect(schema.recipeInstructions).toEqual([
        { '@type': 'HowToStep', position: 1, text: 'Mix apples and spices.' },
        { '@type': 'HowToStep', position: 2, text: 'Fill crust and bake at 375F.' },
      ]);
    });

    it('generates valid parseable JSON-LD string', () => {
      const jsonStr = generateJsonLd(sampleRecipes);
      const parsed = JSON.parse(jsonStr);
      expect(parsed['@type']).toBe('Recipe');
      expect(parsed.name).toBe('Grandma\'s "Secret" Apple Pie');
    });
  });

  describe('Raw JSON Serializer', () => {
    it('serializes array of RecipeSchema directly', () => {
      const raw = generateRawJson(sampleRecipes);
      const parsed = JSON.parse(raw);
      expect(Array.isArray(parsed)).toBe(true);
      expect(parsed[0].recipe_id).toBe('rec_001');
    });
  });

  describe('Obsidian Markdown Serializer (R3)', () => {
    it('generates YAML frontmatter and markdown sections with wikilinks', () => {
      const md = generateMarkdown(sampleRecipes, true);
      expect(md).toContain('---');
      expect(md).toContain('title: "Grandma\'s \\"Secret\\" Apple Pie"');
      expect(md).toContain('tags:');
      expect(md).toContain('  - Dessert');
      expect(md).toContain('# Grandma\'s "Secret" Apple Pie');
      expect(md).toContain('## Ingredients');
      expect(md).toContain('- 6 cups sliced apples');
      expect(md).toContain('## Directions');
      expect(md).toContain('![[card_front_001.jpg]]');
    });
  });

  describe('Cooklang Serializer (R3)', () => {
    it('formats recipe with metadata and ingredient/cookware step annotations', () => {
      const cook = generateCooklang(sampleRecipes);
      expect(cook).toContain('>> title: Grandma\'s "Secret" Apple Pie');
      expect(cook).toContain('>> prep time: 20 mins');
      expect(cook).toContain('-- Ingredients:');
      expect(cook).toContain('@sliced apples{6%cups}');
      expect(cook).toContain('@sugar{3/4%cup}');
      expect(cook).toContain('@cinnamon{1%tbsp}');
    });
  });

  describe('Google Docs HTML Archive Serializer (R1)', () => {
    it('generates rich styled HTML document for Google Docs interchange', () => {
      const html = generateGoogleDocsHtml(sampleRecipes, 'My Heirloom Cookbook');
      expect(html).toContain('<!DOCTYPE html>');
      expect(html).toContain('My Heirloom Cookbook');
      expect(html).toContain('Grandma&#39;s &quot;Secret&quot; Apple Pie');
      expect(html).toContain('meta-table');
      expect(html).toContain('Ingredients');
    });
  });

  describe('ExportDialog UI Component', () => {
    it('switches between all 9 export format tabs cleanly', () => {
      render(<ExportDialog recipes={sampleRecipes} sessionId="sess_export_test" />);

      expect(screen.getByText(/Plan To Eat RFC 4180 CSV/i)).toBeInTheDocument();

      // Click JSON-LD Tab
      const jsonLdTab = screen.getByRole('button', { name: /Schema\.org JSON-LD/i });
      fireEvent.click(jsonLdTab);
      expect(screen.getByText(/Schema\.org Recipe JSON-LD/i)).toBeInTheDocument();

      // Click Raw JSON Tab
      const rawJsonTab = screen.getByRole('button', { name: /Standard Raw JSON/i });
      fireEvent.click(rawJsonTab);
      expect(screen.getByText(/Raw RecipeSchema JSON Array/i)).toBeInTheDocument();

      // Click Obsidian Markdown Tab
      const mdTab = screen.getByRole('button', { name: /Obsidian Markdown/i });
      fireEvent.click(mdTab);
      expect(screen.getByText(/Obsidian Markdown with YAML Frontmatter/i)).toBeInTheDocument();

      // Click Cooklang Tab
      const cookTab = screen.getByRole('button', { name: /Cooklang \(\.cook\)/i });
      fireEvent.click(cookTab);
      expect(screen.getByText(/Cooklang \(\.cook\) Serialized Format/i)).toBeInTheDocument();

      // Click Paprika Tab
      const paprikaTab = screen.getByRole('button', { name: /Paprika \(\.paprikarecipes\)/i });
      fireEvent.click(paprikaTab);
      expect(screen.getByText(/Paprika Recipe Manager/i)).toBeInTheDocument();

      // Click Google Docs Tab
      const gdocsTab = screen.getByRole('button', { name: /Google Docs Archival/i });
      fireEvent.click(gdocsTab);
      expect(screen.getByText(/Google Docs Interchange Archive/i)).toBeInTheDocument();

      // Click Heirloom DOCX Tab
      const docxTab = screen.getByRole('button', { name: /Heirloom DOCX/i });
      fireEvent.click(docxTab);
      expect(screen.getByText(/Heirloom Cookbook DOCX/i)).toBeInTheDocument();

      // Click Printable PDF Tab
      const pdfTab = screen.getByRole('button', { name: /Printable PDF/i });
      fireEvent.click(pdfTab);
      expect(screen.getByText(/Printable Archival PDF Cookbook/i)).toBeInTheDocument();
    });

    it('GAP-04: allows editing filename and updates extension when switching format tabs', () => {
      render(<ExportDialog recipes={sampleRecipes} sessionId="sess_test_123456" />);

      const filenameInput = screen.getByLabelText(/Export Filename:/i) as HTMLInputElement;
      expect(filenameInput).toBeInTheDocument();
      expect(filenameInput.value).toContain('.csv');

      // Edit filename stem
      fireEvent.change(filenameInput, { target: { value: 'grandma_secret_pie_2026.csv' } });
      expect(filenameInput.value).toBe('grandma_secret_pie_2026.csv');

      // Switch to PDF tab -> extension should change to .pdf while preserving custom stem
      const pdfTab = screen.getByRole('button', { name: /Printable PDF/i });
      fireEvent.click(pdfTab);

      expect(filenameInput.value).toBe('grandma_secret_pie_2026.pdf');

      // Switch to Cooklang tab -> extension should change to .cook
      const cookTab = screen.getByRole('button', { name: /Cooklang \(\.cook\)/i });
      fireEvent.click(cookTab);

      expect(filenameInput.value).toBe('grandma_secret_pie_2026.cook');
    });

    it('opens customize filename modal and applies new filename', () => {
      render(<ExportDialog recipes={sampleRecipes} sessionId="sess_test_123456" />);

      const moreOptionsBtn = screen.getByRole('button', { name: /More Options/i });
      fireEvent.click(moreOptionsBtn);

      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(screen.getByText(/Customize Export Filename/i)).toBeInTheDocument();

      const modalInput = screen.getByLabelText(/Filename for/i) as HTMLInputElement;
      fireEvent.change(modalInput, { target: { value: 'custom_holiday_deck.csv' } });

      const applyBtn = screen.getByRole('button', { name: /Apply/i });
      fireEvent.click(applyBtn);

      // Verify modal is closed and input field updated
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      const mainInput = screen.getByLabelText(/Export Filename:/i) as HTMLInputElement;
      expect(mainInput.value).toBe('custom_holiday_deck.csv');
    });
  });

  describe('Google Docs Direct Publishing & OAuth Flow', () => {
    it('handles direct doc creation with token and displays success link', async () => {
      // Mock global fetch for create-document
      const originalFetch = global.fetch;
      global.fetch = vi.fn().mockImplementation((url, init) => {
        if (typeof url === 'string' && url.includes('/api/export/google-docs/create-document')) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                document_id: 'doc_mock_live_123',
                url: 'https://docs.google.com/document/d/doc_mock_live_123/edit',
                title: "RecipeBoxer - Grandma's \"Secret\" Apple Pie",
                recipe_count: 1,
              }),
          } as Response);
        }
        return originalFetch(url, init);
      });

      render(<ExportDialog recipes={sampleRecipes} sessionId="sess_gdocs_oauth" />);

      // Switch to Google Docs tab
      const gdocsTab = screen.getByRole('button', { name: /Google Docs Archival/i });
      fireEvent.click(gdocsTab);

      expect(screen.getByText(/Direct Google Docs & Drive API Publishing/i)).toBeInTheDocument();

      // Open token drawer
      const tokenBtn = screen.getByRole('button', { name: /Token/i });
      fireEvent.click(tokenBtn);

      const tokenInput = screen.getByPlaceholderText(/ya29\./i);
      fireEvent.change(tokenInput, { target: { value: 'ya29.a0Ac_test_token' } });

      const createDocBtn = screen.getByRole('button', { name: /Create Doc/i });
      fireEvent.click(createDocBtn);

      // Wait for success alert and open link
      const successLink = await screen.findByRole('link', { name: /Open in Google Docs/i });
      expect(successLink).toBeInTheDocument();
      expect(successLink).toHaveAttribute(
        'href',
        'https://docs.google.com/document/d/doc_mock_live_123/edit'
      );

      global.fetch = originalFetch;
    });
  });
});

