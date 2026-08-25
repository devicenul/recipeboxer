import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { ExportDialog } from '../src/components/export/ExportDialog';
import { generatePlanToEatCsv, escapeCsvField } from '../src/utils/exportPlanToEat';
import { generateJsonLd, recipeToSchemaOrg } from '../src/utils/exportJsonLd';
import { generateRawJson } from '../src/utils/exportJson';
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

  describe('ExportDialog UI Component', () => {
    it('switches between CSV, JSON-LD, and Raw JSON tabs', () => {
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
    });
  });
});
