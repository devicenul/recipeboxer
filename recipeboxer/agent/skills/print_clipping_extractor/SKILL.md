---
name: print-clipping-extractor
description: "Domain skill for extracting structured recipes from printed newsprint clippings, magazine cutouts, package box panels, and excluding coupons, advertisements, and promotional sidebars."
---

# Print Clipping & Package Label Recipe Extraction Skill

## Overview

The `print-clipping-extractor` skill enables RecipeBoxer agents to accurately transcribe vintage newsprint cutouts, magazine columns, and product package panels (e.g. Crisco, Hershey's, Borden's Eagle Brand, Kraft).

---

## 1. Multi-Column Layout & Reading Flow

Magazine and newspaper clippings frequently use 2-column or 3-column layouts where text flows down Column 1 before proceeding to Column 2.

- **Column Flow Order**: Transcribe ingredients down the entire left column before transitioning to the directions column on the right.
- **Header Isolation**: Extract the overarching recipe title and headnote from across the spanning banner before reading individual columns.
- **Sub-Recipes**: When a clipping contains both main dish and frosting/sauce (e.g. "Frosting:"), retain distinct sub-recipe headers in the ingredients list.

---

## 2. Noise & Promotional Text Exclusion Rules

Newsprint and magazine cutouts frequently include non-culinary advertising copy, coupons, and publisher notices that MUST be strictly excluded from the extracted recipe:

- **Coupons & Special Offers**: Exclude lines like "Clip and save this coupon", "Save 15¢ on your next purchase", "Offer expires Dec 31", barcode numbers, and store redemption addresses.
- **Product Contests & Free Booklets**: Exclude "Send 25¢ and 2 box tops to...", "For our free recipe booklet write to Box 414".
- **Magazine Marginalia**: Exclude issue dates, page numbers, publication names ("Good Housekeeping, Oct 1962, p. 114"), and publisher copyright lines.
- **Advertised Unrelated Products**: Exclude promotional sidebars for unrelated cleaning products, baking pans, or appliances.

---

## 3. Brand & Provenance Attribution

- Extract brand names mentioned in the title or headnote (e.g. "Borden's Eagle Brand Chocolate Pie", "Crisco Southern Fried Chicken") and preserve the brand name in `tags` and `source_notes`.
- Note if the card is a pasted cutout on index paper in `source_notes`.
