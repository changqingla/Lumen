# generate_radar_chart - Radar Chart

## Overview
Compares capability dimensions for one or more objects in a multidimensional coordinate system. Commonly used for evaluations, product comparisons, and performance profiles.

## Input Fields
### Required
- `data`: array<object>. Each record contains `name` (string) and `value` (number), with optional `group` (string).

### Optional
- `style.backgroundColor`: string. Sets the background color.
- `style.lineWidth`: number. Sets radar line width.
- `style.palette`: string[]. Defines series colors.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.
- `title`: string, default empty string.

## Usage Tips
Keep the number of dimensions between 4 and 8. Distinguish different objects with `group` and provide values for each dimension. Normalize values first if dimensions use different units.

## Return Value
- Returns a radar chart URL and includes `_meta.spec`.
