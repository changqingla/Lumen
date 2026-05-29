# generate_scatter_chart - Scatter Chart

## Overview
Shows the relationship between two continuous variables. Color or shape can distinguish groups. Suitable for correlation analysis and cluster exploration.

## Input Fields
### Required
- `data`: array<object>. Each record contains `x` (number) and `y` (number), with optional `group` (string).

### Optional
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
Standardize variables with different units before uploading when appropriate. For very large datasets, sample first. Use `group` to distinguish categories or clustering results.

## Return Value
- Returns a scatter chart URL and includes `_meta.spec`.
