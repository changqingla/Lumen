# generate_spreadsheet - Spreadsheet / Pivot Table

## Overview
Generates a spreadsheet or pivot table for structured tabular data. When `rows` or `values` fields are provided, the result is rendered as a pivot table or cross table. Otherwise, it is rendered as a regular table. Suitable for structured data, cross-category comparisons, and data summaries.

## Input Fields
### Required
- `data`: array<object>. Table data array, where each object represents one row. Keys are column names, and values may be strings, numbers, null, or undefined. Example: `[{ name: 'John', age: 30 }, { name: 'Jane', age: 25 }]`.

### Optional
- `rows`: array<string>. Row header fields for the pivot table. When `rows` or `values` is provided, the spreadsheet is rendered as a pivot table.
- `columns`: array<string>. Column header fields used to specify column order. For regular tables, this determines column order; for pivot tables, it defines column grouping.
- `values`: array<string>. Value fields for the pivot table. When `rows` or `values` is provided, the spreadsheet is rendered as a pivot table.
- `theme`: string, default `default`. Options: `default` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.

## Usage Tips
- For a regular table, provide `data` and optional `columns` to control column order.
- For a pivot table or cross table, provide `rows` for row grouping, `columns` for column grouping, and `values` for aggregated value fields.
- Make sure field names in the data match the field names specified in `rows`, `columns`, and `values`.

## Return Value
- Returns a spreadsheet or pivot table image URL and includes `_meta.spec` for later editing.
