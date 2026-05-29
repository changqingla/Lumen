# generate_liquid_chart - Liquid Chart

## Overview
Displays a single percentage or progress value as liquid fill height. It has strong visual motion and is suitable for completion rate, resource utilization, and similar metrics.

## Input Fields
### Required
- `percent`: number. Range [0, 1], representing the current percentage or progress.

### Optional
- `shape`: string, default `circle`. Options: `circle` / `rect` / `pin` / `triangle`.
- `style.backgroundColor`: string. Custom background color.
- `style.color`: string. Custom liquid wave color.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.
- `title`: string, default empty string.

## Usage Tips
Make sure the percentage is normalized. A single chart supports only one progress value. For multiple metrics, generate several liquid charts side by side. A title can be something like `Goal Completion Rate 85%`.

## Return Value
- Returns a liquid chart URL and records parameters in `_meta.spec`.
