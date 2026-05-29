# generate_network_graph - Network Graph

## Overview
Presents relationships among entities through nodes and links. Suitable for social networks, system dependencies, knowledge graphs, and similar relationship structures.

## Input Fields
### Required
- `data`: object. Required. Contains nodes and edges.
- `data.nodes`: array<object>. At least 1 item, each with a unique `name`.
- `data.edges`: array<object>. At least 1 item, each containing `source` and `target` (string), with optional `name` describing the relationship.

### Optional
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.

## Usage Tips
Keep node count between 10 and 50 to avoid crowding. Make sure every `source` and `target` in `edges` exists in `nodes`. Use labels to clarify relationship meaning.

## Return Value
- Returns a network graph URL and provides `_meta.spec` for later node edits.
