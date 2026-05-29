# generate_sankey_chart - Sankey Chart

## Overview
Shows the direction and quantity of resources, energy, or user flow between nodes. Suitable for budget allocation, traffic paths, energy consumption, and similar flow distributions.

## Input Fields
### Required
- `data`: array<object>. Each record contains `source` (string), `target` (string), and `value` (number).

### Optional
- `nodeAlign`: string, default `center`. Options: `left` / `right` / `justify` / `center`.
- `style.backgroundColor`: string. Sets the background color.
- `style.palette`: string[]. Defines node colors.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.
- `title`: string, default empty string.

## Usage Tips
Keep node names unique and avoid excessive crossings. If loops exist, flatten them into staged flows first. Filter small flows by threshold to focus on the main paths.

## Return Value
- Returns a Sankey chart URL and stores node and flow definitions in `_meta.spec`.
