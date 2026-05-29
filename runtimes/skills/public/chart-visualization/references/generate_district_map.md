# generate_district_map - Administrative District Map (China)

## Overview
Generates a coverage or heat map for provinces, cities, districts, or counties in China. It can show metric ranges, categories, or regional composition and is suitable for regional sales, policy coverage, and similar scenarios.

## Input Fields
### Required
- `title`: string. Required and no more than 16 characters. Describes the map topic.
- `data`: object. Required. Contains administrative district configuration and metric information.
- `data.name`: string. Required. A China administrative district keyword that must be specific to province, city, district, or county level.

### Optional
- `data.style.fillColor`: string. Custom fill color for no-data areas.
- `data.colors`: string[]. Enumerated or continuous color bands. A 10-color list is provided by default.
- `data.dataType`: string. Enum `number` / `enum`; determines the color mapping method.
- `data.dataLabel`: string. Metric name, such as `GDP`.
- `data.dataValue`: string. Metric value or enum label.
- `data.dataValueUnit`: string. Metric unit, such as `trillion`.
- `data.showAllSubdistricts`: boolean, default `false`. Whether to show all lower-level administrative regions.
- `data.subdistricts[]`: array<object>. Used for drilling into subregions. Each item must contain at least `name` and may include `dataValue` and `style.fillColor`.
- `width`: number, default `1600`. Sets image width.
- `height`: number, default `1000`. Sets image height.

## Usage Tips
Names must be precise to the administrative level to avoid ambiguity. If `subdistricts` is configured, also enable `showAllSubdistricts`. The map supports only locations within China and depends on Amap data.

## Return Value
- Returns a map image URL and keeps the complete input in `_meta.spec`. If `SERVICE_ID` is configured, the generated record is also synchronized to the map mini app.
