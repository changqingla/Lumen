# generate_dual_axes_chart - Dual-Axis Chart

## Overview
Overlays columns and lines, or two curves with different units, on the same canvas. Useful for showing trend and comparison together, such as revenue versus profit or temperature versus rainfall.

## Input Fields
### Required
- `categories`: string[]. Ordered X-axis ticks, such as years, months, or categories.
- `series`: array<object>. Each item must include at least `type` (`column` / `line`) and `data` (number[] with the same length as `categories`). Optional `axisYTitle` (string) describes that series' Y-axis meaning.

### Optional
- `style.backgroundColor`: string. Custom background color.
- `style.palette`: string[]. Configures colors for multiple series.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.
- `title`: string, default empty string.
- `axisXTitle`: string, default empty string.

## Usage Tips
Use only when different units or a combined legend comparison are truly needed. Keep the number of series at 2 or fewer to avoid complexity. If two curves differ greatly in magnitude, use the secondary axis for scaling.

## Return Value
- Returns a dual-axis chart image URL and includes detailed parameters in `_meta.spec`.
