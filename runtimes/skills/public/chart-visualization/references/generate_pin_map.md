# generate_pin_map - Pin Map (China)

## Overview
Displays multiple POI locations as markers on a map of China. Popups can show images or notes. Suitable for store distribution, asset placement, and similar location sets.

## Input Fields
### Required
- `title`: string. Required and no more than 16 characters. Summarizes the set of locations.
- `data`: string[]. Required. Contains POI names within China.

### Optional
- `markerPopup.type`: string. Fixed as `image`.
- `markerPopup.width`: number, default `40`. Image width.
- `markerPopup.height`: number, default `40`. Image height.
- `markerPopup.borderRadius`: number, default `8`. Image corner radius.
- `width`: number, default `1600`.
- `height`: number, default `1000`.

## Usage Tips
POI names should include enough geographic qualification, such as city plus landmark. Business attributes can be included in names, such as `Shanghai Xuhui Store A`. The map depends on Amap data and supports only China.

## Return Value
- Returns a pin map URL and stores location and popup configuration in `_meta.spec`.
