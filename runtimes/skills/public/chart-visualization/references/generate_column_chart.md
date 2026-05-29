# generate_column_chart - Column Chart

## Overview
Compares metrics across categories or time periods with vertical columns. Supports grouped or stacked display and is commonly used for sales, revenue, and traffic comparisons.

## Input Fields
### Required
- `data`: array<object>. Each item must include at least `category` (string) and `value` (number). For grouped or stacked charts, include `group` (string).

### Optional
- `group`: boolean, default `true`. Displays different `group` values side by side. Requires `stack=false` and data containing `group`.
- `stack`: boolean, default `false`. Stacks different `group` values into the same column. Requires `group=false` and data containing `group`.
- `style.backgroundColor`: string. Custom background color.
- `style.palette`: string[]. Defines the color palette.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.
- `title`: string, default empty string.
- `axisXTitle`: string, default empty string.
- `axisYTitle`: string, default empty string.

## Usage Tips
When there are many categories, more than about 12, use Top-N filtering or aggregation. In stacked mode, make sure every record includes the `group` field to avoid validation failures.

## Return Value
- Returns a column chart URL and provides configuration details in `_meta.spec`.
