# Spatial operations

Area, perimeter, centroids, buffers in metres, set operations, AggGeom unions, simplification, polygon splitting and MGRS references.

**Use for:** computing polygon areas and perimeters; drawing a radius around a point; combining or intersecting regions; merging many geometries into one per group; reducing vertex counts; reprojecting to a metric coordinate system; converting points to and from MGRS references.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds both tables | `retail_demo` |
| `points_table` | Table of point geometries | `stores` |
| `point_id` | Key of the points table | `store_id` |
| `point_geom` | Point geometry column | `location` |
| `group_column` | Column of the points table to union by | `city` |
| `regions_table` | Table of polygon geometries | `regions` |
| `region_id` | Key of the regions table | `region_id` |
| `region_geom` | Polygon geometry column | `boundary` |
| `geo_srid` | Teradata SRID of WGS 84 longitude/latitude | `1619` |
| `metric_srid` | Teradata SRID of a projected system in metres covering the data (here WGS 84 / UTM zone 14N, EPSG 32614) | `3052` |
| `radius_m` | Buffer radius in metres | `5000` |
| `mgrs_precision` | MGRS digits per axis: 1 = 10 km … 5 = 1 m | `3` |

## Statements

### area_and_perimeter — Area, perimeter, centroid of polygons

Results are in the coordinate units: on longitude/latitude data, square degrees and degrees, which are not areas. `ST_Centroid` can lie outside a concave polygon; `ST_PointOnSurface` is always inside. Both are refused on lines and points (Error 7548). `ST_NumPoints` is refused on a polygon; count the vertices of its exterior ring.

```sql
SELECT {{region_id}}, region_name,
  {{region_geom}}.ST_Area() AS area_coord_units,
  {{region_geom}}.ST_Perimeter() AS perimeter_coord_units,
  {{region_geom}}.ST_Centroid().ST_AsText() AS centroid,
  {{region_geom}}.ST_PointOnSurface().ST_AsText() AS point_on_surface,
  {{region_geom}}.ST_ExteriorRing().ST_NumPoints() AS ring_vertices
FROM {{database}}.{{regions_table}}
ORDER BY {{region_id}};
```

### find_metric_srid — The Teradata SRID of a projected system

Look a projected system up by its EPSG code, then use that SRID and its definition text for `ST_Transform`.

```sql
SELECT SRID, AUTH_SRID, SUBSTR(SRTEXT, 1, 60) AS definition
FROM SYSSPATIAL.SPATIAL_REF_SYS
WHERE AUTH_NAME = 'EPSG' AND AUTH_SRID IN (4326, 32614);
```

### buffer_in_metres — A radius in metres around longitude/latitude points

`ST_Buffer` works in coordinate units, so `ST_Buffer(0.05)` on longitude/latitude is 0.05 degrees — a different real size at every latitude. For metres: give the geometry its SRID, reproject to a metric system with the three-argument `ST_Transform(target_srid, target_srtext, source_srtext)`, buffer, and reproject back. The single-argument form does not exist (Error 6834). A 5,000 m buffer has an area of about 78.5 km².

```sql
SELECT p.{{point_id}},
  CAST(p.{{point_geom}}.ST_SRID({{geo_srid}})
        .ST_Transform({{metric_srid}}, t.SRTEXT, f.SRTEXT)
        .ST_Buffer({{radius_m}}).ST_Area() / 1000000 AS DECIMAL(10,2)) AS buffer_km2,
  p.{{point_geom}}.ST_SRID({{geo_srid}})
    .ST_Transform({{metric_srid}}, t.SRTEXT, f.SRTEXT)
    .ST_Buffer({{radius_m}})
    .ST_Transform({{geo_srid}}, f.SRTEXT, t.SRTEXT)
    .ST_Envelope().ST_AsText() AS buffer_extent_lonlat
FROM {{database}}.{{points_table}} AS p,
  SYSSPATIAL.SPATIAL_REF_SYS AS f,
  SYSSPATIAL.SPATIAL_REF_SYS AS t
WHERE f.SRID = {{geo_srid}} AND t.SRID = {{metric_srid}}
  AND p.{{point_id}} IN (1, 2)
ORDER BY p.{{point_id}};
```

### buffer_rectangle — A rectangle around a point, sized in metres

`ST_SpheroidalBufferMBR(metres)` returns the bounding rectangle of a radius around a longitude/latitude point as text `(minx,miny,maxx,maxy)` — no reprojection needed when a rectangle is enough.

```sql
SELECT {{point_id}}, {{point_geom}}.ST_SpheroidalBufferMBR(CAST({{radius_m}} AS FLOAT)) AS rectangle_lonlat
FROM {{database}}.{{points_table}}
ORDER BY {{point_id}};
```

### set_operations — Union, intersection, difference of two regions

```sql
SELECT a.{{region_id}} AS region_a, b.{{region_id}} AS region_b,
  a.{{region_geom}}.ST_Intersection(b.{{region_geom}}).ST_AsText() AS shared_area,
  a.{{region_geom}}.ST_Union(b.{{region_geom}}).ST_Area() AS union_area,
  a.{{region_geom}}.ST_Difference(b.{{region_geom}}).ST_Area() AS a_without_b,
  a.{{region_geom}}.ST_SymDifference(b.{{region_geom}}).ST_Area() AS either_not_both
FROM {{database}}.{{regions_table}} AS a
JOIN {{database}}.{{regions_table}} AS b ON a.{{region_id}} < b.{{region_id}}
WHERE a.{{region_geom}}.ST_Overlaps(b.{{region_geom}}) = 1;
```

### union_per_group — AggGeom: one merged geometry per group

AggGeom is a table operator; with `PARTITION BY` it returns one geometry per group. It returns the geometry first, then the partition column. Points merge into a MULTIPOINT.

```sql
SELECT {{group_column}}, geom.ST_AsText() AS merged
FROM AggGeom(
  ON (SELECT {{point_geom}} AS geom, {{group_column}}
      FROM {{database}}.{{points_table}}
      WHERE {{group_column}} IS NOT NULL)
  PARTITION BY {{group_column}}
  USING Operation('Union')
) AS u
ORDER BY {{group_column}};
```

### union_of_all — AggGeom: one geometry for the whole table

Without `PARTITION BY`, AggGeom returns one partial union per AMP, not one geometry (here two rows for three regions). A second pass over a constant partition merges them.

```sql
SELECT geom.ST_AsText() AS merged, geom.ST_Area() AS area
FROM AggGeom(
  ON (SELECT partial.geom, 1 AS p
      FROM AggGeom(
        ON (SELECT {{region_geom}} AS geom FROM {{database}}.{{regions_table}}
            WHERE {{region_id}} IN (1, 2, 4))
        USING Operation('Union')
      ) AS partial)
  PARTITION BY p
  USING Operation('Union')
) AS g;
```

### simplify — Fewer vertices, topology kept

The tolerance is in coordinate units; vertices that deviate less than it from a straight line are removed.

```sql
SELECT g.ST_ExteriorRing().ST_NumPoints() AS vertices_before,
  g.SimplifyPreserveTopology(0.05).ST_ExteriorRing().ST_NumPoints() AS vertices_after,
  g.SimplifyPreserveTopology(0.05).ST_AsText() AS simplified
FROM (SELECT NEW ST_Geometry('POLYGON((0 0, 1 0.01, 2 0, 3 0.02, 4 0, 4 4, 0 4, 0 0))') AS g) AS t;
```

### polygon_split — Split polygons into pieces of bounded vertex count

PolygonSplit cuts each polygon into quadrants until every piece has fewer than `max_vertices` vertices (minimum 10); polygons already under the limit come back whole, as piece 0.

```sql
SELECT *
FROM PolygonSplit(ON (
  SELECT {{region_id}}, {{region_geom}} AS geom, CAST(10 AS INTEGER) AS max_vertices
  FROM {{database}}.{{regions_table}}
)) AS s ({{region_id}}, piece_id, piece)
ORDER BY {{region_id}}, piece_id;
```

### mgrs — Points to MGRS references and back

`TO_MGRS(point, srtext, digits)` truncates to the precision; `FROM_MGRS(reference, srtext, srid)` returns the south-west corner of the cell as a point.

```sql
SELECT p.{{point_id}},
  TO_MGRS(p.{{point_geom}}, s.SRTEXT, {{mgrs_precision}}) AS mgrs_reference,
  FROM_MGRS(TO_MGRS(p.{{point_geom}}, s.SRTEXT, 5), s.SRTEXT, {{geo_srid}}).ST_AsText() AS back_to_point
FROM {{database}}.{{points_table}} AS p, SYSSPATIAL.SPATIAL_REF_SYS AS s
WHERE s.SRID = {{geo_srid}}
ORDER BY p.{{point_id}};
```

## Common errors

- **Error 6834 Method ST_Transform( ... ) does not exist** — `ST_Transform` called with only a target SRID. Fix: pass `(target_srid, target_srtext, source_srtext)`, the two texts read from `SYSSPATIAL.SPATIAL_REF_SYS`.
- **Error 7548 ... cannot call ST_NumPoints on geometry object with type ST_Polygon** — `ST_NumPoints` is for lines. Fix: `geom.ST_ExteriorRing().ST_NumPoints()`.
- **Error 7548 ... cannot call ST_Centroid on ... ST_LineString** — Centroids are defined for (multi)polygons. Fix: filter by `ST_GeometryType()`, or take a point along the line.
- **A union per table returns several rows** — AggGeom without `PARTITION BY` returns one partial result per AMP. Fix: the two-pass form (union_of_all).
- **Areas or buffers in degrees** — Constructive operations work in coordinate units. Fix: reproject to a metric system first (buffer_in_metres).
- **Error 3707 near AS overlaps** — `OVERLAPS` is reserved. Fix: name the column something else.

## Practices

- Keep longitude/latitude storage in SRID 1619 and reproject to a metric system only for measures that need metres; pick the projection that covers the data (a UTM zone for a region, an equal-area projection for a country).
- Use two-pass AggGeom whenever one geometry for the whole input is wanted.
- Simplify only for display; analysis on simplified shapes changes areas and predicates.
