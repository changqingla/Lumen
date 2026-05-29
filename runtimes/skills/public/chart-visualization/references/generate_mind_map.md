# generate_mind_map - Mind Map

## Overview
Expands 2-3 levels of branches around a central topic to organize ideas, plans, or knowledge structures. Commonly used for brainstorming and solution planning.

## Input Fields
### Required
- `data`: object. Required. Each node must contain at least `name`; expand recursively through `children` (array<object>). Recommended depth is 3 or less.

### Optional
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.

## Usage Tips
Use the central node for the topic. First-level branches should represent major dimensions such as goals, resources, or risks. Leaf nodes should use short phrases. If there are many branches, split them into multiple mind maps.

## Return Value
- Returns a mind map URL and keeps the node tree in `_meta.spec` for later refinement.
