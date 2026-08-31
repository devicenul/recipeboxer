/**
 * Export Curation and Grouping Types for RecipeBoxer Phase 9 Milestone 3 (R3)
 */

import { RecipeSchema } from './recipe';

export type GroupingPreset =
  | 'none'
  | 'course'
  | 'contributor'
  | 'tags'
  | 'alphabetical'
  | 'custom';

export interface CuratedSection {
  section_id: string;
  title: string;
  subtitle?: string | null;
  recipes: RecipeSchema[];
}

export interface CuratedCookbook {
  cookbook_title: string;
  grouping_preset: GroupingPreset;
  sections: CuratedSection[];
  total_recipe_count: number;
  include_section_dividers: boolean;
  include_toc: boolean;
  toc_title: string;
}

export interface ExportCurationOptions {
  recipe_order?: string[];
  grouping_preset: GroupingPreset;
  include_section_dividers: boolean;
  include_toc: boolean;
  toc_title: string;
}
