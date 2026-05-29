# generate_path_map - Route Map (China)

## Overview
Displays routes or trips within China using Amap, connecting a sequence of POIs in order. Suitable for logistics routes, travel planning, and delivery tracks.

## Input Fields
### Required
- `title`: string. Required and no more than 16 characters. Describes the route topic.
- `data`: array<object>. At least 1 route object.
- `data[].data`: string[]. Required. Contains POI names within China arranged in route order.

### Optional
- `width`: number, default `1600`.
- `height`: number, default `1000`.

## Usage Tips
POI names must be specific and located in China, such as `Xi'an Bell Tower` or `Su Causeway at West Lake, Hangzhou`. For multiple routes, add multiple route objects to `data`.

## Return Value
- Returns a route map URL and keeps the title and POI list in `_meta.spec`. If `SERVICE_ID` is configured, the result is also recorded in the map app.
