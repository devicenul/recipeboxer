import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { ExportDialog } from '../src/components/export/ExportDialog';
import { ExportCurationToolbar } from '../src/components/export/ExportCurationToolbar';
import { RecipeSchema } from '../src/types/recipe';
import {
  curateRecipes,
  normalizeTitleForSorting,
  extractContributorName,
  reorderRecipes,
  matchCourseTaxonomy,
} from '../src/utils/exportCuration';
import { ExportCurationOptions, CuratedCookbook } from '../src/types/export';

describe('Phase 9: Export Curation, Reordering & Grouping (Milestone 3 / R3)', () => {
  const sampleRecipes: RecipeSchema[] = [
    {
      recipe_id: 'rec_001',
      title: 'The Spinach Artichoke Dip',
      description: 'Creamy warm party dip.',
      yield_servings: '8 servings',
      prep_time: '10 mins',
      cook_time: '20 mins',
      total_time: '30 mins',
      ingredients: ['1 cup spinach', '1 can artichokes', '1 cup cream cheese'],
      directions: ['1. Mix ingredients.', '2. Bake at 375F.'],
      tags: ['Appetizer', 'Party'],
      source_notes: "Aunt Clara's game day favorite.",
      card_ids: ['card_01'],
    },
    {
      recipe_id: 'rec_002',
      title: "Grandma Rose's Apple Strudel",
      description: 'Paper-thin rolled pastry with cinnamon apples.',
      yield_servings: '8 servings',
      prep_time: '45 mins',
      cook_time: '40 mins',
      total_time: '1 hr 25 mins',
      ingredients: ['4 cups apples', 'Phyllo dough', '1 cup sugar'],
      directions: ['1. Roll dough.', '2. Bake at 375F.'],
      tags: ['Dessert', 'Heirloom'],
      source_notes: "Grandma Rose's recipe box",
      card_ids: ['card_02'],
    },
    {
      recipe_id: 'rec_003',
      title: "Grandpa's Prime Rib",
      description: 'Herb crusted holiday roast.',
      yield_servings: '10 servings',
      prep_time: '20 mins',
      cook_time: '2 hrs',
      total_time: '2 hrs 20 mins',
      ingredients: ['6 lb prime rib', 'Garlic', 'Rosemary'],
      directions: ['1. Season meat.', '2. Roast at 325F.'],
      tags: ['Main Course', 'Holiday'],
      source_notes: "Grandpa Joe's Christmas tradition.",
      card_ids: ['card_03'],
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('1. Curation Engine Utilities', () => {
    it('normalizeTitleForSorting strips leading English articles', () => {
      expect(normalizeTitleForSorting('The Heirloom Apple Pie')).toBe('Heirloom Apple Pie');
      expect(normalizeTitleForSorting('A Delightful Cobbler')).toBe('Delightful Cobbler');
      expect(normalizeTitleForSorting('An Apple Crisp')).toBe('Apple Crisp');
      expect(normalizeTitleForSorting('Simple Roast Chicken')).toBe('Simple Roast Chicken');
    });

    it('extractContributorName extracts clean family and contributor attribution', () => {
      expect(extractContributorName("Grandma Clara's Kitchen (Summer 1964)")).toBe('Grandma Clara');
      expect(extractContributorName("Aunt Mabel's Sunday Suppers")).toBe('Aunt Mabel');
      expect(extractContributorName('From the kitchen of Uncle Bob')).toBe('Uncle Bob');
      expect(extractContributorName('Handed down by Nana')).toBe('Nana');
      expect(extractContributorName('')).toBe('Family Collection / Unattributed');
    });

    it('reorderRecipes correctly sequences recipes based on recipe_order ID array', () => {
      const reordered = reorderRecipes(sampleRecipes, ['rec_003', 'rec_001']);
      expect(reordered.map((r) => r.recipe_id)).toEqual(['rec_003', 'rec_001', 'rec_002']);
    });

    it('matchCourseTaxonomy identifies appetizers, mains, and desserts', () => {
      expect(matchCourseTaxonomy(sampleRecipes[0])?.id).toBe('course_appetizers_starters');
      expect(matchCourseTaxonomy(sampleRecipes[1])?.id).toBe('course_desserts_sweets');
      expect(matchCourseTaxonomy(sampleRecipes[2])?.id).toBe('course_main_courses');
    });

    it('curateRecipes creates course grouped sections', () => {
      const curated = curateRecipes(sampleRecipes, { grouping_preset: 'course' });
      expect(curated.grouping_preset).toBe('course');
      expect(curated.total_recipe_count).toBe(3);
      expect(curated.sections.length).toBe(3);
      expect(curated.sections.map((s) => s.title)).toEqual([
        'Appetizers & Starters',
        'Main Courses & Entrées',
        'Desserts & Sweets',
      ]);
    });

    it('curateRecipes creates contributor grouped sections', () => {
      const curated = curateRecipes(sampleRecipes, { grouping_preset: 'contributor' });
      expect(curated.grouping_preset).toBe('contributor');
      expect(curated.sections.length).toBeGreaterThanOrEqual(2);
    });

    it('curateRecipes creates alphabetical sorted sections', () => {
      const curated = curateRecipes(sampleRecipes, { grouping_preset: 'alphabetical' });
      expect(curated.grouping_preset).toBe('alphabetical');
      const titles = curated.sections[0].recipes.map((r) => r.title);
      // 'Grandma Rose', 'Grandpa', 'The Spinach...' ('Spinach...')
      expect(titles).toEqual([
        "Grandma Rose's Apple Strudel",
        "Grandpa's Prime Rib",
        'The Spinach Artichoke Dip',
      ]);
    });
  });

  describe('2. ExportCurationToolbar Component', () => {
    const defaultOptions: ExportCurationOptions = {
      recipe_order: ['rec_001', 'rec_002', 'rec_003'],
      grouping_preset: 'none',
      include_section_dividers: true,
      include_toc: true,
      toc_title: 'Table of Contents',
    };

    it('renders all 6 preset buttons and toggles', () => {
      const mockChange = vi.fn();
      const cookbook = curateRecipes(sampleRecipes, defaultOptions);

      render(
        <ExportCurationToolbar
          recipes={sampleRecipes}
          curatedCookbook={cookbook}
          curationOptions={defaultOptions}
          onChangeOptions={mockChange}
        />
      );

      expect(screen.getByText(/Natural Order/i)).toBeDefined();
      expect(screen.getByText(/By Course/i)).toBeDefined();
      expect(screen.getByText(/By Contributor/i)).toBeDefined();
      expect(screen.getByText(/By Tag/i)).toBeDefined();
      expect(screen.getByText(/A to Z/i)).toBeDefined();
      expect(screen.getByText(/Custom Order/i)).toBeDefined();
      expect(screen.getByLabelText(/Include Table of Contents/i)).toBeDefined();
      expect(screen.getByLabelText(/Include Section Dividers/i)).toBeDefined();
    });

    it('calls onChangeOptions when switching preset', () => {
      const mockChange = vi.fn();
      const cookbook = curateRecipes(sampleRecipes, defaultOptions);

      render(
        <ExportCurationToolbar
          recipes={sampleRecipes}
          curatedCookbook={cookbook}
          curationOptions={defaultOptions}
          onChangeOptions={mockChange}
        />
      );

      fireEvent.click(screen.getByText(/By Course/i));
      expect(mockChange).toHaveBeenCalledWith(
        expect.objectContaining({ grouping_preset: 'course' })
      );
    });

    it('allows toggling manual recipe sequence drawer and shows position badges', () => {
      const mockChange = vi.fn();
      const cookbook = curateRecipes(sampleRecipes, defaultOptions);

      render(
        <ExportCurationToolbar
          recipes={sampleRecipes}
          curatedCookbook={cookbook}
          curationOptions={defaultOptions}
          onChangeOptions={mockChange}
        />
      );

      const sequenceBtn = screen.getByText(/Custom Sequence/i);
      fireEvent.click(sequenceBtn);

      expect(screen.getByText(/#1/i)).toBeDefined();
      expect(screen.getByText(/#2/i)).toBeDefined();
      expect(screen.getByText(/#3/i)).toBeDefined();
    });
  });

  describe('3. ExportDialog Integration', () => {
    it('renders full ExportDialog with Curation toolbar and preview tabs', () => {
      render(
        <ExportDialog
          isOpen={true}
          onClose={vi.fn()}
          recipes={sampleRecipes}
          sessionId="sess_curation_test_01"
        />
      );

      expect(screen.getByText(/Multi-Format Recipe Export & Archival Publishing/i)).toBeDefined();
      expect(screen.getByText(/Cookbook Curation & Recipe Sequencing/i)).toBeDefined();
      expect(screen.getAllByText(/Plan To Eat CSV/i).length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText(/Obsidian Markdown/i).length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText(/Google Docs Archival/i).length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText(/Heirloom DOCX/i).length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText(/Printable PDF/i).length).toBeGreaterThanOrEqual(1);
    });

    it('updates TOC title and switches tabs seamlessly', () => {
      render(
        <ExportDialog
          isOpen={true}
          onClose={vi.fn()}
          recipes={sampleRecipes}
          sessionId="sess_curation_test_01"
        />
      );

      // Switch to Markdown
      const mdButtons = screen.getAllByRole('button', { name: /Obsidian Markdown/i });
      fireEvent.click(mdButtons[0]);

      // Switch to DOCX
      const docxButtons = screen.getAllByRole('button', { name: /Heirloom DOCX/i });
      fireEvent.click(docxButtons[0]);

      // Check download button reflects active tab
      expect(screen.getByRole('button', { name: /Download DOCX Cookbook/i })).toBeDefined();
    });
  });
});
