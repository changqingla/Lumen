# generate_organization_chart - Organization Chart

## Overview
Shows hierarchical relationships within a company, team, or project, and can describe roles or responsibilities on nodes.

## Input Fields
### Required
- `data`: object. Required. Nodes must contain at least `name` (string), with optional `description` (string). Child nodes are nested through `children` (array<object>). Maximum recommended depth is 3.

### Optional
- `orient`: string, default `vertical`. Options: `horizontal` / `vertical`.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.

## Usage Tips
Use positions or roles as node names, and use `description` to briefly describe responsibilities or headcount. If the organization is large, split it into multiple subcharts or render by department.

## Return Value
- Returns an organization chart URL and saves the structure in `_meta.spec` for later iteration.
