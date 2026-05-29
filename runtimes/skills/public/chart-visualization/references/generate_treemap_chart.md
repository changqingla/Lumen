# generate_treemap_chart - Treemap Chart

## Overview
Shows hierarchical structures and node weights using nested rectangles. Suitable for asset shares, market shares, directory capacity, and similar composition views.

## Input Fields
### Required
- `data`: array<object>. Node array. Each item contains `name` (string) and `value` (number), and may recursively include `children`.

### Optional
- `style.backgroundColor`: string. Sets the background color.
- `style.palette`: string[]. Defines the color palette.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.
- `title`: string, default empty string.

## Usage Tips
Ensure each node `value` is non-negative and consistent with the sum of child nodes. Avoid overly deep trees; aggregate in advance when needed. Add value units to node names when it improves readability.

## Return Value
- Returns a treemap chart URL and synchronizes `_meta.spec`.
