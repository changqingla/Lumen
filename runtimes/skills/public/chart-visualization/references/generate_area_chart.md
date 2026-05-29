# generate_area_chart - Area Chart

## Overview
Shows numeric trends over a continuous independent variable, usually time. Stacking can be enabled to observe cumulative contributions from different groups. Suitable for KPI, energy, production, and other time-series scenarios.

## Input Fields
### Required
- `data`: array. Each item contains `time` (string) and `value` (number). For stacked charts, include `group` (string). At least 1 record is required.

### Optional
- `stack`: boolean, default `false`. When enabled, every data item must include a `group` field.
- `style.backgroundColor`: string. Sets the chart background color, such as `#fff`.
- `style.lineWidth`: number. Customizes the area boundary line width.
- `style.palette`: string[]. Provides a color palette for series coloring.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`. Controls chart width.
- `height`: number, default `400`. Controls chart height.
- `title`: string, default empty string. Sets the chart title.
- `axisXTitle`: string, default empty string. Sets the X-axis title.
- `axisYTitle`: string, default empty string. Sets the Y-axis title.

## Usage Tips
Keep the `time` field format consistent, such as `YYYY-MM`. In stacked mode, each group should cover the same time points; fill missing values before rendering when needed.

## Return Value
- Returns an image URL and includes the complete area chart configuration in `_meta.spec` for rerendering or traceability.
