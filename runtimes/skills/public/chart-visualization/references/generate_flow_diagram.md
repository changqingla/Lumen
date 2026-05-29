# generate_flow_diagram - Flow Diagram

## Overview
Shows business processes, approval chains, or algorithm steps with nodes and edges. Supports multiple node types such as start, decision, and operation nodes.

## Input Fields
### Required
- `data`: object. Required. Contains node and edge definitions.
- `data.nodes`: array<object>. At least 1 item. Each node must provide a unique `name`.
- `data.edges`: array<object>. At least 1 item. Each edge contains `source` and `target` (string), with optional `name` as edge text.

### Optional
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.

## Usage Tips
List node `name` values first and keep them unique, then create edges. If conditions need to be described, put them in `edges.name`. Keep the process one-directional or make branches explicit to avoid crossings.

## Return Value
- Returns a flow diagram URL and includes node and edge data in `_meta.spec` for later adjustment.
