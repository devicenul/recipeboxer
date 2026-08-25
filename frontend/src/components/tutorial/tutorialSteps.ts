export interface HotkeyHint {
  key: string;
  label: string;
}

export interface TutorialStep {
  id: string;
  stepNumber: number;
  totalSteps: number;
  title: string;
  badge: string;
  targetTab: 'ingest' | 'hitl' | 'recipes' | 'export';
  targetSelector: string;
  description: string;
  highlights: string[];
  hotkeys?: HotkeyHint[];
  iconName: 'Layers' | 'GitMerge' | 'LayoutGrid' | 'SlidersHorizontal' | 'BookOpen' | 'Download';
}

export const TUTORIAL_STORAGE_KEY = 'recipeboxer_tutorial_completed';

export const TUTORIAL_STEPS: TutorialStep[] = [
  {
    id: 'ingestion-sources',
    stepNumber: 1,
    totalSteps: 6,
    title: 'Ingestion & Pluggable Sources',
    badge: 'Step 1 of 6: Ingestion',
    targetTab: 'ingest',
    targetSelector: '[data-tour="ingestion-sources"]',
    description:
      'Import recipe card collections from multiple sources including local scan directories, multi-file uploads, Google Photos albums, and Cloud Drive (S3/GCS buckets). Configure max concurrency and slow-batch pacing to control API quota consumption.',
    highlights: [
      'Pluggable Sources: Local Folders, Drag-and-Drop Scans, Google Photos & Cloud Drive',
      'API Quota Throttle: Slow-batch chunk size and delay pacing controls',
      'Dynamic Thinking Mode: Configurable reasoning budget for degraded cursive',
    ],
    iconName: 'Layers',
  },
  {
    id: 'rapid-queue-hotkeys',
    stepNumber: 2,
    totalSteps: 6,
    title: 'Rapid HITL Queue & Hotkeys',
    badge: 'Step 2 of 6: Pairing Queue',
    targetTab: 'hitl',
    targetSelector: '[data-tour="rapid-queue"]',
    description:
      'Review proposed front and back card pairings at lightning speed. Use single-key hotkeys for instantaneous decision-making without touching the mouse.',
    hotkeys: [
      { key: 'Space', label: 'Approve Pair' },
      { key: 'S', label: 'Mark as Standalone' },
      { key: 'B', label: 'Reverse Front / Back' },
      { key: 'X', label: 'Reject Scan' },
      { key: 'Tab', label: 'Open Candidate Drawer' },
    ],
    highlights: [
      'Fast single-keystroke approvals with zero UI lag',
      'Instant front/back swap with visual orientation update',
      'Mark single-sided cards or discard non-recipe artifacts',
    ],
    iconName: 'GitMerge',
  },
  {
    id: 'candidate-drawer-multicard',
    stepNumber: 3,
    totalSteps: 6,
    title: 'Candidate Drawer & Multi-Card Sets',
    badge: 'Step 3 of 6: Multi-Card Sets',
    targetTab: 'hitl',
    targetSelector: '[data-tour="candidate-drawer"]',
    description:
      'Inspect AI correlation candidate rankings with visual confidence scores. When a recipe spans 3 or 4 cards, attach additional cards directly into multi-part recipe groups with custom sequence reordering.',
    highlights: [
      'Visual confidence scoring for top alternate pairing candidates',
      '3+ part recipe grouping (Card 1 Front, Card 1 Back, Card 2 Front, Card 2 Back)',
      'Interactive card tray with drag/button reordering and detach capabilities',
    ],
    iconName: 'LayoutGrid',
  },
  {
    id: 'image-inspection-tools',
    stepNumber: 4,
    totalSteps: 6,
    title: 'Image Inspection Tools',
    badge: 'Step 4 of 6: Visual Tools',
    targetTab: 'hitl',
    targetSelector: '[data-tour="image-tools"]',
    description:
      'Examine degraded, yellowed, and faint pencil scans with precision inspection tools. Synchronize front and back zoom/pan, perform 90° rotations, and apply real-time contrast, brightness, and color inversion filters.',
    highlights: [
      'Synchronized or independent pan and zoom up to 400%',
      '90° Clockwise and Counter-Clockwise lossless image rotation',
      'Adaptive image filters: Brightness, Contrast enhancement, and Negative inversion',
    ],
    iconName: 'SlidersHorizontal',
  },
  {
    id: 'recipe-editor',
    stepNumber: 5,
    totalSteps: 6,
    title: 'Split-Screen Recipe Editor',
    badge: 'Step 5 of 6: Recipe Editor',
    targetTab: 'recipes',
    targetSelector: '[data-tour="recipe-editor"]',
    description:
      'Verify and edit AI-transcribed recipes side-by-side with original card scans. Modify structured ingredient quantities/units, reorder numbered instructions, adjust prep/cook times, and manage taxonomy tags with real-time SQLite persistence.',
    highlights: [
      '50/50 split-screen layout with card scan carousel and synchronized zoom',
      'Structured ingredient list editor with fraction normalization',
      'Step-by-step direction management and instant SQLite autosave',
    ],
    iconName: 'BookOpen',
  },
  {
    id: 'multi-format-export',
    stepNumber: 6,
    totalSteps: 6,
    title: 'Multi-Format Export Matrix',
    badge: 'Step 6 of 6: Export Matrix',
    targetTab: 'export',
    targetSelector: '[data-tour="export-matrix"]',
    description:
      'Publish your digitized recipe collection across 9 supported formats: Plan To Eat RFC 4180 CSV, Schema.org JSON-LD, Google Docs Archival, Heirloom DOCX, Printable PDF, Obsidian Markdown, Cooklang (.cook), Paprika (.paprikarecipes), and Raw JSON.',
    highlights: [
      'Primary Plan To Eat CSV (RFC 4180 compliant) and Schema.org Recipe JSON-LD',
      'Print-ready Heirloom DOCX & styled PDF cookbooks with embedded card photos',
      'Culinary manager formats: Paprika, Cooklang, Obsidian Markdown, and Google Docs',
    ],
    iconName: 'Download',
  },
];
