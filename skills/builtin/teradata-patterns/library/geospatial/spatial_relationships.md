# Spatial relationships

Point in polygon, containment, intersection, touching and overlapping regions, DE-9IM patterns, planar versus spherical distance.

**Use for:** assigning points to the regions that contain them; spatial joins between two tables; finding geometries near a location; measuring distances in metres between longitude/latitude points; testing whether regions share a border or overlap.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds both tables | `retail_demo` |
| `points_table` | Table of point geometries | `stores` |
| `point_id` | Key of the points table | `store_id` |
| `point_geom` | Point geometry column | `location` |
| `regions_table` | Table of polygon geometries | `regions` |
| `region_id` | Key of the regions table | `region_id` |
| `region_geom` | Polygon geometry column | `boundary` |
| `search_wkt` | Search polygon, WKT, in the same SRID as the tables | `POLYGON((-103 29, -86 29, -86 43, -103 43, -103 29))` |
| `center_wkt` | Search point, WKT | `POINT(-97.7431 30.2672)` |
| `radius_m` | Search radius in metres | `500000` |

## Statements

### point_in_polygon — The region(s) each point falls in, and the points that fall in none

`ST_Within` is 1 only for a point inside the polygon; a point exactly on the boundary returns 0 (`ST_Intersects` and `ST_Touches` return 1 for it). Where regions overlap, a point matches each of them and an INNER JOIN counts it twice — check `regions_matched`. The LEFT JOIN keeps points that fall in no region.

```sql
SELECT p.{{point_id}},
  r.{{region_id}}, r.region_name,
  COUNT(r.{{region_id}}) OVER (PARTITION BY p.{{point_id}}) AS regions_matched
FROM {{database}}.{{points_table}} AS p
LEFT JOIN {{database}}.{{regions_table}} AS r
  ON p.{{point_geom}}.ST_Within(r.{{region_geom}}) = 1
ORDER BY p.{{point_id}}, r.{{region_id}};
```

### points_per_region — Spatial join with a count per region

A region with no points still appears with 0.

```sql
SELECT r.{{region_id}}, r.region_name, COUNT(p.{{point_id}}) AS points_inside
FROM {{database}}.{{regions_table}} AS r
LEFT JOIN {{database}}.{{points_table}} AS p
  ON r.{{region_geom}}.ST_Contains(p.{{point_geom}}) = 1
GROUP BY r.{{region_id}}, r.region_name
ORDER BY r.{{region_id}};
```

### inside_search_area — Geometries inside a search polygon

`a.ST_Within(b) = 1` and `b.ST_Contains(a) = 1` are the same test. Write the predicate as a method call compared to 1 against a constant geometry; that form can use the geospatial index.

```sql
SELECT {{point_id}}, {{point_geom}}.ST_AsText() AS wkt
FROM {{database}}.{{points_table}}
WHERE {{point_geom}}.ST_Within(NEW ST_Geometry('{{search_wkt}}')) = 1
ORDER BY {{point_id}};
```

### region_pairs — How each pair of regions relates

`ST_Touches`: they share a border but no interior. `ST_Overlaps`: their interiors share area and neither contains the other. `ST_Intersects`: any common point. `ST_Relate` tests a DE-9IM pattern; `'F***1****'` means "interiors do not meet, boundaries share a line" — a common border.

```sql
SELECT a.region_name AS region_a, b.region_name AS region_b,
  a.{{region_geom}}.ST_Touches(b.{{region_geom}}) AS touches_border,
  a.{{region_geom}}.ST_Overlaps(b.{{region_geom}}) AS interiors_overlap,
  a.{{region_geom}}.ST_Intersects(b.{{region_geom}}) AS any_contact,
  a.{{region_geom}}.ST_Relate(b.{{region_geom}}, 'F***1****') AS shares_border_line
FROM {{database}}.{{regions_table}} AS a
JOIN {{database}}.{{regions_table}} AS b ON a.{{region_id}} < b.{{region_id}}
WHERE a.{{region_geom}}.ST_Intersects(b.{{region_geom}}) = 1
ORDER BY 1, 2;
```

### route_crossings — Which regions a line crosses

Call `ST_Crosses` on the LINE. `polygon.ST_Crosses(line)` returns 0 even when the line runs through the polygon; `line.ST_Crosses(polygon)` returns 1. A line that stays inside one polygon does not cross it.

```sql
SELECT r.{{region_id}}, r.region_name,
  NEW ST_Geometry('LINESTRING(-110 35, -90 35)').ST_Crosses(r.{{region_geom}}) AS route_crosses,
  r.{{region_geom}}.ST_Crosses(NEW ST_Geometry('LINESTRING(-110 35, -90 35)')) AS reversed_call
FROM {{database}}.{{regions_table}} AS r
ORDER BY r.{{region_id}};
```

### distance_units — Planar distance is in coordinate units, not metres

On longitude/latitude data `ST_Distance` returns degrees. `ST_SphericalDistance` (great circle) and `ST_SpheroidalDistance` (ellipsoid) return metres and expect longitude/latitude points. A degree of longitude is about 111 km at the equator and shrinks with latitude, so a degree threshold is not a fixed distance.

```sql
SELECT a.{{point_id}} AS from_id, b.{{point_id}} AS to_id,
  CAST(a.{{point_geom}}.ST_Distance(b.{{point_geom}}) AS DECIMAL(10,4)) AS distance_degrees,
  CAST(a.{{point_geom}}.ST_SphericalDistance(b.{{point_geom}}) / 1000 AS DECIMAL(10,1)) AS spherical_km,
  CAST(a.{{point_geom}}.ST_SpheroidalDistance(b.{{point_geom}}) / 1000 AS DECIMAL(10,1)) AS spheroidal_km
FROM {{database}}.{{points_table}} AS a
JOIN {{database}}.{{points_table}} AS b ON a.{{point_id}} < b.{{point_id}}
WHERE a.{{point_id}} = 1
ORDER BY spherical_km;
```

### within_radius — Points within a radius in metres

Filter in metres with `ST_SphericalDistance`. A pre-filter with a planar `ST_Distance < constant` in degrees can use the geospatial index; keep it generous (here 10 degrees) so the metre test decides.

```sql
SELECT {{point_id}},
  CAST({{point_geom}}.ST_SphericalDistance(NEW ST_Geometry('{{center_wkt}}')) / 1000 AS DECIMAL(10,1)) AS km
FROM {{database}}.{{points_table}}
WHERE {{point_geom}}.ST_Distance(NEW ST_Geometry('{{center_wkt}}')) < 10.0
  AND {{point_geom}}.ST_SphericalDistance(NEW ST_Geometry('{{center_wkt}}')) <= {{radius_m}}
ORDER BY km;
```

### nearest_neighbour — The closest other point to each point

```sql
SELECT a.{{point_id}}, b.{{point_id}} AS nearest_id,
  CAST(a.{{point_geom}}.ST_SphericalDistance(b.{{point_geom}}) / 1000 AS DECIMAL(10,1)) AS km
FROM {{database}}.{{points_table}} AS a
JOIN {{database}}.{{points_table}} AS b ON a.{{point_id}} <> b.{{point_id}}
QUALIFY ROW_NUMBER() OVER (PARTITION BY a.{{point_id}}
                           ORDER BY a.{{point_geom}}.ST_SphericalDistance(b.{{point_geom}})) = 1
ORDER BY a.{{point_id}};
```

### mbr_prefilter — A cheap bounding-rectangle test

`MBR_Filter` compares bounding rectangles only. It returns every geometry whose rectangle meets the search rectangle — a superset of the exact answer — so follow it with the exact predicate.

```sql
SELECT {{point_id}}
FROM {{database}}.{{points_table}}
WHERE {{point_geom}}.MBR_Filter(NEW ST_Geometry('{{search_wkt}}')) = 1
  AND {{point_geom}}.ST_Within(NEW ST_Geometry('{{search_wkt}}')) = 1
ORDER BY {{point_id}};
```

## Common errors

- **Error 7548 ... SpheroidalDistance: Distance calculation failed to converge** — The two points are near-antipodal (almost opposite sides of the globe). Fix: use `ST_SphericalDistance` for such pairs.
- **Error 7548 ... cannot have unequal srid's** — The geometries carry different SRIDs, typically a `NEW ST_Geometry('...')` literal (SRID 0) against a column stored with SRID 1619. Fix: give the literal the column's SRID: `NEW ST_Geometry('POINT(...)', 1619)`.
- **Distances that look far too small** — `ST_Distance` on longitude/latitude returns degrees. Fix: `ST_SphericalDistance` for metres.
- **Points counted twice** — Overlapping regions both contain the point; the join returns one row per match. Fix: decide which region wins (`QUALIFY ROW_NUMBER() ...`) or count points per region on purpose.
- **A route reported as crossing nothing** — `polygon.ST_Crosses(line)` returns 0. Fix: `line.ST_Crosses(polygon)`.
- **A point on a border is in no region** — `ST_Within` excludes the boundary. Fix: use `ST_Intersects` when a point on the border should count.

## Practices

- Checked with EXPLAIN on 10,000 indexed points: `geom.ST_Within(constant) = 1` and `geom.ST_Distance(constant) < number` are planned through the spatial index; `ST_Disjoint` is an all-rows scan.
- Store longitude/latitude data with SRID 1619 and measure in metres with `ST_SphericalDistance`; keep planar `ST_Distance` for projected data or as an index pre-filter.
- Where regions can overlap, say which one a point belongs to before aggregating.
