# generate_venn_chart - Venn Chart

## Overview
Shows intersections, unions, and differences among multiple sets. Suitable for market segmentation, feature coverage, and user overlap analysis.

## Input Fields
### Required
- `data`: array<object>. Each record contains `value` (number) and `sets` (string[]), with optional `label` (string).

### Optional
- `style.backgroundColor`: string. Sets the background color.
- `style.palette`: string[]. Defines the color palette.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.
- `title`: string, default empty string.

## Usage Tips
Use 4 or fewer sets when possible. If exact weights are unavailable, approximate proportions may be used. Keep set names concise and clear, such as `Mobile Users`.

## Return Value
- Returns a Venn chart URL and stores the configuration in `_meta.spec`.
