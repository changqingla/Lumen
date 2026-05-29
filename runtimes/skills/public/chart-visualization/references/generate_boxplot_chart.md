# generate_boxplot_chart - Box Plot

## Overview
Shows the distribution range of data across categories, including minimum, quartiles, and outliers. Useful for quality monitoring, experimental results, and population distribution comparisons.

## Input Fields
### Required
- `data`: array<object>. Each record contains `category` (string) and `value` (number). Optional `group` (string) supports multi-group comparison.

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
Provide at least 5 samples per category for statistical meaning. For multiple batches, use `group` or generate separate charts.

## Return Value
- Returns a box plot URL and stores the input specification in `_meta.spec`.
