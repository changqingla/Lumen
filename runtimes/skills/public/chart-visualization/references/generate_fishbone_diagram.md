# generate_fishbone_diagram - Fishbone Diagram

## Overview
Used for root-cause analysis. The central problem is placed on the spine, and branches on both sides show categories of causes and detailed subcauses. Common in quality management and process optimization.

## Input Fields
### Required
- `data`: object. Required. Must provide at least the root node `name`; expand recursively with `children` (array<object>). Maximum recommended depth is 3.

### Optional
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.

## Usage Tips
Use the spine node for the problem statement. Use first-level branches for cause categories, such as people, machines, materials, and methods. Use leaf nodes for specific observations and keep them as short phrases.

## Return Value
- Returns a fishbone diagram URL and saves the tree structure in `_meta.spec` for later node edits.
