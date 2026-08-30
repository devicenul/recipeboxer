export enum CardRole {
  STANDALONE = "STANDALONE",
  SEQUENCE_START = "SEQUENCE_START",
  SEQUENCE_CONTINUATION = "SEQUENCE_CONTINUATION",
  UNKNOWN = "UNKNOWN",
}


export enum PaperColorTone {
  WHITE = "White",
  OFF_WHITE_CREAM = "Off-White / Cream",
  AGED_YELLOW = "Aged Yellow",
  KRAFT_BROWN = "Kraft Brown",
  COLORED_CARDSTOCK = "Colored Cardstock",
}

export enum PaperRulingType {
  STANDARD_RULED_BLUE = "Standard Ruled (Blue)",
  STANDARD_RULED_RED_HEADER = "Standard Ruled with Red Header",
  GRID_GRAPH = "Grid / Graph Paper",
  UNRULED_BLANK = "Unruled Blank",
  PRINTED_RECIPE_TEMPLATE = "Printed Recipe Template",
}

export enum InkColor {
  BLUE = "Blue",
  BLACK = "Black",
  RED = "Red",
  GREEN = "Green",
  PENCIL_GRAPHITE = "Pencil Graphite",
  TYPED_RIBBON = "Typewritten Ribbon",
  PRINTED_OFFSET = "Printed Offset",
  OTHER = "Other",
}

export enum InkType {
  BLUE_BALLPOINT = "Blue Ballpoint",
  BLACK_BALLPOINT = "Black Ballpoint",
  FOUNTAIN_PEN = "Fountain Pen Liquid Ink",
  FELT_TIP = "Felt Tip / Marker",
  PENCIL = "Pencil Graphite",
  TYPEWRITER = "Typewriter",
  PRINTED = "Pre-printed Template",
}

export enum ArtifactType {
  OIL_GREASE_STAIN = "Oil / Grease Stain",
  WATER_RING = "Water Ring",
  COFFEE_TEA_SPILL = "Coffee / Tea Spill",
  FOOD_RESIDUE = "Food Residue / Batter",
  TORN_CORNER = "Torn Corner",
  CREASE_FOLD = "Crease / Fold Mark",
  TAPE_RESIDUE = "Tape Residue",
  PUNCH_HOLE = "Punch Hole / Binder Ring",
  AGING_FOXING = "Foxing / Age Spots",
}

export enum CardQuadrant {
  TOP_LEFT = "Top-Left",
  TOP_RIGHT = "Top-Right",
  BOTTOM_LEFT = "Bottom-Left",
  BOTTOM_RIGHT = "Bottom-Right",
  CENTER = "Center",
  EDGE_PERIMETER = "Edge Perimeter",
}

export interface PaperColorInfo {
  hex_approx: string;
  lab_l: number;
  lab_a: number;
  lab_b: number;
  tone_name?: string;
  tone_enum?: PaperColorTone;
}

export interface PaperRulingInfo {
  has_ruling: boolean;
  ruling_type: PaperRulingType | string;
  line_spacing_pixels?: number;
  has_red_header_margin?: boolean;
}

export interface InkInfo {
  ink_color: InkColor | string;
  ink_type: InkType | string;
  is_handwritten?: boolean;
  is_faded?: boolean;
}

export interface PhysicalArtifact {
  artifact_type: ArtifactType | string;
  quadrant: CardQuadrant | string;
  size_relative?: string;
  is_bleed_through_candidate?: boolean;
}

export interface BoundaryText {
  first_line: string;
  last_line: string;
  has_continuation_symbol?: boolean;
  is_cut_off_mid_sentence?: boolean;
  detected_recipe_title?: string | null;
}

export interface VisualFingerprint {
  card_id: string;
  image_path?: string;
  sha256_hash?: string;
  role: CardRole | string;
  role_confidence?: number;
  paper_color?: PaperColorInfo;
  ruling?: PaperRulingInfo;
  ink?: InkInfo;
  artifacts?: PhysicalArtifact[];
  boundary_text?: BoundaryText;
  extracted_at?: string;
}

export interface ScannedCard {
  card_id: string;
  file_path: string;
  filename: string;
  sha256_hash: string;
  file_size_bytes?: number;
  width?: number;
  height?: number;
  format?: string;
  mode?: string;
}
