# generate_pie_chart - Pie / Donut Chart

## Overview
Shows part-to-whole proportions. An inner radius can create a donut chart. Suitable for market share, budget composition, and user segment breakdowns.

## Input Fields
### Required
- `data`: array<object>. Each record contains `category` (string) and `value` (number).

### Optional
- `innerRadius`: number. Range [0, 1], default `0`. Values such as `0.6` generate a donut chart.
- `style.backgroundColor`: string. Sets the background color.
- `style.palette`: string[]. Defines the color palette.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.
- `title`: string, default empty string.

## Usage Tips
Keep category count at 6 or fewer when possible. If there are more, aggregate small categories into `Other`. Ensure values use a consistent unit, either percentage or absolute value, and explain the base in the title when needed.

## Return Value
- Returns a pie or donut chart URL and includes `_meta.spec`.
