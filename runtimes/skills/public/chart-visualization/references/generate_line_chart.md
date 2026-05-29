# generate_line_chart - Line Chart

## Overview
Shows trends over time or another continuous independent variable. Supports multi-series comparison and is suitable for KPI monitoring, metric forecasting, and trend analysis.

## Input Fields
### Required
- `data`: array<object>. Each item contains `time` (string) and `value` (number). For multi-series charts, include `group` (string).

### Optional
- `style.lineWidth`: number. Custom line width.
- `style.backgroundColor`: string. Sets the background color.
- `style.palette`: string[]. Specifies series colors.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.
- `title`: string, default empty string.
- `axisXTitle`: string, default empty string.
- `axisYTitle`: string, default empty string.

## Usage Tips
Time points should be aligned across all series. Use ISO-like formats such as `2025-01-01` or `2025-W01`. For high-frequency data, aggregate to daily or weekly granularity first to avoid excessive density.

## Return Value
- Returns a line chart URL and includes `_meta.spec` for later editing.
