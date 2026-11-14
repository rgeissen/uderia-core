# ST_Geometry basics

Storing points, lines and polygons in an ST_Geometry column: construction from WKT, SRIDs, sizing, the geospatial index, validity and reading geometries back.

**Use for:** creating a table with a geometry column and its geospatial index; loading points and polygons from Well-Known Text; giving geometries a spatial reference system; checking validity after a load; reading coordinates, types and bounding rectangles; choosing the transform group a client receives.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `database` | Database that holds the geometry tables | `retail_demo` |
| `table` | New table that receives the geometries | `site_locations` |
| `geometry_column` | ST_Geometry column of the new table | `location` |
| `srid` | Teradata SRID. WGS 84 longitude/latitude is **1619** here, not 4326 | `1619` |
| `max_length` | Largest geometry, in bytes | `8000` |
| `inline_length` | Geometries up to this size stay in the row; larger ones are stored as LOBs | `8000` |
| `points_table` | Existing table with point geometries | `stores` |

## Statements

### create_geometry_table — A geometry column, its geospatial index, and its registration

`INDEX (location)` creates the geospatial NUSI that spatial predicates can use. It is refused on a volatile table (Error 9500). `AddGeometryColumn` takes an eleventh, OUT argument that returns a message; without it the call fails with Error 3812. The universe MBR is in the SRID's units, degrees for WGS 84.

```sql
CREATE MULTISET TABLE {{database}}.{{table}} (
  site_id INTEGER NOT NULL,
  site_name VARCHAR(50),
  {{geometry_column}} ST_Geometry({{max_length}}) INLINE LENGTH {{inline_length}}
)
PRIMARY INDEX (site_id)
INDEX ({{geometry_column}});

CALL SYSSPATIAL.AddGeometryColumn(
  '', '{{database}}', '{{table}}', '{{geometry_column}}',
  {{srid}}, 'ST_Point', -180.0, -90.0, 180.0, 90.0, result_message);
```

### find_srid — The Teradata SRID for an EPSG code

Teradata numbers spatial reference systems itself. EPSG 4326 (WGS 84) is SRID 1619; there is no SRID 4326 row, yet `NEW ST_Geometry(wkt, 4326)` accepts the number without checking it.

```sql
SELECT SRID, AUTH_NAME, AUTH_SRID, SUBSTR(SRTEXT, 1, 60) AS definition
FROM SYSSPATIAL.SPATIAL_REF_SYS
WHERE AUTH_NAME = 'EPSG' AND AUTH_SRID = 4326;
```

### insert_points — Points from WKT, with and without an SRID

WKT is longitude first: `POINT (x y)`. A bare WKT string gets SRID 0; the constructor sets the SRID. Two geometries with different SRIDs cannot be compared: `ST_Distance`, `ST_Within` and the rest fail with Error 7548, so give every row of a column the same SRID.

```sql
INSERT INTO {{database}}.{{table}} VALUES (1, 'Austin depot',
  NEW ST_Geometry('POINT(-97.7431 30.2672)', {{srid}}));

INSERT INTO {{database}}.{{table}} VALUES (2, 'Chicago depot',
  NEW ST_Geometry('ST_Point', -87.6298, 41.8781, {{srid}}));

INSERT INTO {{database}}.{{table}} VALUES (3, 'Unreferenced', 'POINT(-80.1918 25.7617)');
```

### insert_polygons — Polygons, a polygon with a hole, a multipolygon

A ring is closed by repeating its first point. The interior ring is the hole.

```sql
INSERT INTO {{database}}.{{table}} VALUES (10, 'Square',
  NEW ST_Geometry('POLYGON((0 0, 0 20, 20 20, 20 0, 0 0))', {{srid}}));

INSERT INTO {{database}}.{{table}} VALUES (11, 'Square with a hole',
  NEW ST_Geometry('POLYGON((0 0, 0 20, 20 20, 20 0, 0 0), (5 5, 5 10, 10 10, 10 5, 5 5))', {{srid}}));

INSERT INTO {{database}}.{{table}} VALUES (12, 'Two parts',
  NEW ST_Geometry('MULTIPOLYGON(((1 1, 1 3, 6 3, 6 0, 1 1)), ((10 5, 10 10, 20 10, 20 5, 10 5)))', {{srid}}));
```

### read_geometries — Type, SRID, dimension and WKT of every row

`ST_IsValid`, `ST_IsEmpty` and `ST_Is3D` return 1 or 0.

```sql
SELECT site_id, site_name,
  {{geometry_column}}.ST_GeometryType() AS geom_type,
  {{geometry_column}}.ST_SRID() AS srid,
  {{geometry_column}}.ST_Dimension() AS dimension,
  {{geometry_column}}.ST_IsValid() AS is_valid,
  {{geometry_column}}.ST_Is3D() AS is_3d,
  {{geometry_column}}.ST_AsText() AS wkt
FROM {{database}}.{{table}}
ORDER BY site_id;
```

### point_coordinates — Longitude and latitude of point rows

`ST_X` and `ST_Y` are defined for points only; on a polygon they fail with Error 7548.

```sql
SELECT store_id, store_name,
  location.ST_X() AS longitude,
  location.ST_Y() AS latitude
FROM {{database}}.{{points_table}}
ORDER BY store_id;
```

### bounding_rectangles — Minimum bounding rectangle and extents

`ST_MBR` returns the rectangle as text `(minx,miny,maxx,maxy)`; `ST_Envelope` returns it as a geometry.

```sql
SELECT site_id,
  {{geometry_column}}.ST_MBR() AS mbr,
  {{geometry_column}}.ST_MinX() AS min_x, {{geometry_column}}.ST_MinY() AS min_y,
  {{geometry_column}}.ST_MaxX() AS max_x, {{geometry_column}}.ST_MaxY() AS max_y,
  {{geometry_column}}.ST_Envelope().ST_AsText() AS envelope
FROM {{database}}.{{table}}
ORDER BY site_id;
```

### validity_check — Invalid geometries load without an error

An unclosed ring and a self-intersecting ("bowtie") polygon are both accepted on insert and only `ST_IsValid` reports them. Measures on them are wrong without a warning: the bowtie's `ST_Area` is 0, the open square's is 1. Check validity after every load.

```sql
INSERT INTO {{database}}.{{table}} VALUES (20, 'Open ring',
  NEW ST_Geometry('POLYGON((0 0, 1 0, 1 1, 0 1))', {{srid}}));

INSERT INTO {{database}}.{{table}} VALUES (21, 'Bowtie',
  NEW ST_Geometry('POLYGON((0 0, 10 10, 10 0, 0 10, 0 0))', {{srid}}));

SELECT site_id, site_name,
  {{geometry_column}}.ST_IsValid() AS is_valid,
  {{geometry_column}}.ST_Area() AS area
FROM {{database}}.{{table}}
WHERE {{geometry_column}}.ST_Dimension() = 2
ORDER BY site_id;
```

### transform_group — What a client receives for a geometry column

The default transform sends WKT as a CLOB. `TD_GEO_VARCHAR` sends VARCHAR WKT; `TD_GEO_VARCHAR_SRID` sends EWKT with the SRID first (`SRID=0;POINT (...)`). The setting lasts for the session.

```sql
SET TRANSFORM GROUP FOR TYPE SYSUDTLIB.ST_Geometry TD_GEO_VARCHAR_SRID;

SELECT store_id, location
FROM {{database}}.{{points_table}}
WHERE store_id <= 3
ORDER BY store_id;
```

### unregister_geometry_column — Remove the registration before dropping the table

A dropped table leaves its row in `SYSSPATIAL.GEOMETRY_COLUMNS` unless the column is unregistered first.

```sql
CALL SYSSPATIAL.DropGeometryColumn('', '{{database}}', '{{table}}', '{{geometry_column}}', result_message);

SELECT COUNT(*) AS still_registered
FROM SYSSPATIAL.GEOMETRY_COLUMNS
WHERE F_TABLE_SCHEMA = '{{database}}' AND F_TABLE_NAME = '{{table}}';
```

## Common errors

- **Error 7548 ... cannot have unequal srid's** — The two geometries of a spatial method carry different SRIDs (often one inserted as a bare WKT string, SRID 0). Fix: give every geometry of a column the same SRID with the constructor, or reassign with `ST_SRID(new_srid)`.
- **Error 7548 ... geometry type must be ST_Point** — `ST_X`/`ST_Y` called on a line or polygon. Fix: use `ST_Centroid()` or `ST_MinX()`/`ST_MaxX()` for non-points.
- **Error 3812 The positional assignment list has too few values** — `AddGeometryColumn` or `DropGeometryColumn` called without the OUT argument. Fix: add a final argument name (`result_message`).
- **Error 9500 CDT Indexes are not supported in volatile tables** — `INDEX (geom)` on a volatile table. Fix: use a permanent table when the query needs the geospatial index.
- **No SRID 4326** — Teradata's WGS 84 SRID is 1619; 4326 is the EPSG code. `NEW ST_Geometry(wkt, 4326)` is accepted, but `SPATIAL_REF_SYS` has no row for it. Fix: look the SRID up by `AUTH_SRID` (find_srid).
- **Error 3707 near SET TRANSFORM GROUP ... TO** — There is no `TO`. Fix: `SET TRANSFORM GROUP FOR TYPE SYSUDTLIB.ST_Geometry TD_GEO_VARCHAR;`.

## Practices

- Size point columns small and in-row (`ST_Geometry(8000) INLINE LENGTH 8000`); a large polygon column needs a larger maximum and stores what exceeds the inline length as a LOB.
- Pick one SRID per column, look it up in `SYSSPATIAL.SPATIAL_REF_SYS`, and set it with the constructor on every insert.
- Run the validity check after every bulk load; invalid polygons give wrong areas and wrong predicates without failing.
- Unregister a geometry column before dropping its table.
