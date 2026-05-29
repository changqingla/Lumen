# generate_bar_chart - Bar Chart

## Overview
Compares metrics across categories or groups with horizontal bars. Suitable for Top-N rankings and comparisons across regions, channels, or segments.

## Input Fields
### Required
- `data`: array<object>. Each item must include at least `category` (string) and `value` (number). For grouped or stacked charts, also provide `group` (string).

### Optional
- `group`: boolean, default `false`. When enabled, different `group` values are shown side by side. Requires `stack=false` and data with a `group` field.
- `stack`: boolean, default `true`. When enabled, different `group` values are stacked in the same bar. Requires `group=false` and data with a `group` field.
- `style.backgroundColor`: string. Custom background color, such as `#fff`.
- `style.palette`: string[]. Sets the series color list.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`. Controls chart width.
- `height`: number, default `400`. Controls chart height.
- `title`: string, default empty string. Sets the chart title.
- `axisXTitle`: string, default empty string. Sets the X-axis title.
- `axisYTitle`: string, default empty string. Sets the Y-axis title.

## Usage Tips
Keep category names short. If there are many series, use stacking or filter to the most important items to avoid visual clutter.

## Return Value
- Returns a bar chart image URL and includes the complete configuration in `_meta.spec` for reuse.
