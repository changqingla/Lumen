# generate_histogram_chart - Histogram

## Overview
Shows frequency or probability distribution of continuous values through bins, making it easier to identify skewness, outliers, and concentration ranges.

## Input Fields
### Required
- `data`: number[]. At least 1 value, used to build the frequency distribution.

### Optional
- `binNumber`: number. Custom number of bins. If omitted, it is estimated automatically.
- `style.backgroundColor`: string. Sets the background color.
- `style.palette`: string[]. Defines bar colors.
- `style.texture`: string, default `default`. Options: `default` / `rough`.
- `theme`: string, default `default`. Options: `default` / `academy` / `dark`.
- `width`: number, default `600`.
- `height`: number, default `400`.
- `title`: string, default empty string.
- `axisXTitle`: string, default empty string.
- `axisYTitle`: string, default empty string.

## Usage Tips
Remove missing values and anomalies before uploading. A sample size of at least 30 is recommended. Adjust `binNumber` according to business meaning to balance detail and overall trend.

## Return Value
- Returns a histogram URL and stores parameters in `_meta.spec`.
