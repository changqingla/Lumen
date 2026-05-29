# generate_word_cloud_chart - Word Cloud Chart

## Overview
Adjusts word size and placement according to frequency or weight. Useful for quickly extracting text themes, sentiment, or keyword hotspots.

## Input Fields
### Required
- `data`: array<object>. Each record contains `text` (string) and `value` (number).

### Optional
- `style.backgroundColor`: string. Sets the background color.
- `style.palette`: string[]. Defines word cloud colors.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.
- `title`: string, default empty string.

## Usage Tips
Remove stop words and merge synonyms before generation. Normalize case to avoid duplicates. If sentiment should be emphasized, map positive and negative values to colors.

## Return Value
- Returns a word cloud chart URL and includes `_meta.spec`.
