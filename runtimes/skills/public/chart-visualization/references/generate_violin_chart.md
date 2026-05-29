# generate_violin_chart - Violin Chart

## Overview
Combines kernel density curves with box-plot statistics to show distribution shapes across categories. Suitable for comparing multiple experiment batches or population performance.

## Input Fields
### Required
- `data`: array<object>. Each record contains `category` (string) and `value` (number), with optional `group` (string).

### Optional
- `style.backgroundColor`: string. Sets the background color.
- `style.palette`: string[]. Defines the color palette.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.
- `title`: string, default empty string.
- `axisXTitle`: string, default empty string.
- `axisYTitle`: string, default empty string.

## Usage Tips
At least 30 samples per category are recommended for stable density estimation. If quartile information should be emphasized, combine this with a box plot.

## Return Value
- Returns a violin chart URL and keeps the configuration in `_meta.spec`.
