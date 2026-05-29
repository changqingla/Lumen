# generate_funnel_chart - Funnel Chart

## Overview
Shows conversion or drop-off across multiple stages. Commonly used for sales pipelines, user journeys, and other stepwise filtering processes.

## Input Fields
### Required
- `data`: array<object>. Records must be ordered by process stage. Each item contains `category` (string) and `value` (number).

### Optional
- `style.backgroundColor`: string. Sets the background color.
- `style.palette`: string[]. Defines colors for stages.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.
- `title`: string, default empty string.

## Usage Tips
Stage order must match the actual process. If values are percentages, use a consistent denominator and explain the basis in the title or notes. Avoid too many stages; 6 or fewer is recommended.

## Return Value
- Returns a funnel chart URL and includes `_meta.spec` for reuse.
