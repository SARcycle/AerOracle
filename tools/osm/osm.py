"""
OSM tunnels, bridges and region outlines for AerOracle (used by update.py).

Tunnels and bridges, per Geofabrik region:
1. Download the region's .osm.pbf (MD5 checked, progress output every 30 s). The next region downloads while the current one
   is processed: one download and one processing at a time, at most two .pbf files on disk.
2. One `osmium tags-filter` pass: ways with tunnel=yes or any bridge tag, plus their nodes. Without osmium, GDAL
   reads the whole .pbf (slower, same result).
3. Per type, in GeoPackage: road classes (highway *ary*, unclassified, residential, living_street, pedestrian, road,
   cycleway; not bicycle=no) and tunnel=yes or bridge=*. Then projection to UTM, simplification by a tolerance of SIMPLIFY_M,
   bridges at least MIN_BRIDGE_M long, buffered by BUFFER_M, merged and split into single polygons, reprojection to WGS84.
   Output: <folder>/<name>.gpkg, one layer.
Outlines: the region's KML, made valid, merged and split into single polygons: <folder>/<name>.gpkg.
Favourite regions get a number prefix (1_switzerland.gpkg ...): the app checks them first.
"""
import hashlib
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))  # Project root: config
import config

TMP_DIR = os.path.join(config.TEMP_DIR, 'OSM')  # .pbf of up to a few GB

BUFFER_M = 30           # Radius around each tunnel or bridge line, in metres
SIMPLIFY_M = 5          # Douglas-Peucker tolerance of the lines, in metres
ROAD_FILTER = ("(highway LIKE '%ary%' OR highway IN ('unclassified', 'residential', 'living_street', 'pedestrian', "
               "'road', 'cycleway')) AND (bicycle IS NULL OR bicycle <> 'no')")   # osmfilter: highway=*ary* =... --drop bicycle=no
TYPE_FILTER = {'tunnels': "tunnel = 'yes'", 'bridges': 'bridge IS NOT NULL'}       # tunnel=yes, bridge=*
MIN_BRIDGE_M = 25       # The former filter measured 50 m in Web Mercator: true 34 m in Switzerland, 25 m in Sweden


def log(message):
    print(message, flush=True)


def osm_config(folder):
    """GDAL OSM driver configuration with tunnel, bridge and bicycle as columns of the lines layer."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, 'osmconf_aeroracle.ini')
    if not os.path.exists(path):
        source = subprocess.run(['gdal-config', '--datadir'], capture_output=True, text=True).stdout.strip()
        text = open(os.path.join(source or '/usr/share/gdal', 'osmconf.ini')).read()
        i, j = text.index('[lines]'), text.index('[multipolygons]')
        block = re.sub(r'\nattributes=.*', '\nattributes=name,highway,tunnel,bridge,bicycle', text[i:j])
        block = re.sub(r'\n(computed_attributes|z_order_type|z_order_sql)=', r'\n#\1=', block)
        open(path, 'w').write(text[:i] + block + text[j:])
    return path


def download_pbf(url, folder, retries=5):
    """The region's .pbf in folder (MD5 checked against Geofabrik's .md5); returns (path, seconds)."""
    path = os.path.join(folder, os.path.basename(url))
    started = time.time()
    for attempt in range(retries):
        try:
            with requests.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                total = int(r.headers.get('content-length', 0))
                md5, done, shown, t0 = hashlib.md5(), 0, 0.0, time.time()
                with open(path, 'wb') as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk); md5.update(chunk); done += len(chunk)
                        if time.time() - shown >= 30:  # Progress every 30 s
                            shown = time.time()
                            rate = done / max(shown - t0, 1e-9) / 1e6
                            left = (total - done) / 1e6 / rate if total and rate else 0
                            log(f'\t\t[download {os.path.basename(url).replace("-latest.osm.pbf", "")}] '
                                f'{done / 1e6:.0f} of {total / 1e6:.0f} MB ({done / max(total, 1):.0%}), '
                                f'{rate:.1f} MB/s, about {left / 60:.0f} min left')
            expected = requests.get(url + '.md5', timeout=30).text.split()[0]
            if md5.hexdigest() == expected:
                return path, time.time() - started
            log(f'\t\t- MD5 mismatch (attempt {attempt + 1})')
        except Exception as e:
            log(f'\t\t- Download failed (attempt {attempt + 1}): {e}')
        time.sleep(5)
    raise RuntimeError(f'Download of {url} failed after {retries} attempts')


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'{cmd[0]} failed: {result.stderr.strip()[-500:]}')


def prefilter(pbf, folder):
    """Ways with tunnel=yes or any bridge tag: one osmium pass; the .pbf itself without osmium."""
    if not shutil.which('osmium'):
        return pbf, 0.0
    out = os.path.join(folder, os.path.basename(pbf).replace('.osm.pbf', '-tb.osm.pbf'))
    started = time.time()
    run(['osmium', 'tags-filter', pbf, 'w/tunnel=yes', 'w/bridge', '-o', out, '--overwrite', '--no-progress'])
    return out, time.time() - started


def utm_epsg(lon, lat):
    """EPSG code of the UTM zone of a point (WGS84)."""
    return (32600 if lat >= 0 else 32700) + min(int((lon + 180) // 6) + 1, 60)


def buffer_metric(lines, kind):
    """
    Polygons around the lines, in metres: grouped by UTM zone, simplified by SIMPLIFY_M, bridges at least MIN_BRIDGE_M long,
    buffered by BUFFER_M, merged per zone, back to WGS84, merged across zone borders, split into single polygons.
    """
    import geopandas as gpd
    import shapely
    if lines.empty:
        return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
    points = lines.geometry.representative_point()
    zones = [utm_epsg(p.x, p.y) for p in points]
    parts = []
    for epsg in sorted(set(zones)):
        group = lines[[z == epsg for z in zones]].to_crs(epsg=epsg)
        geoms = shapely.simplify(group.geometry.values, SIMPLIFY_M)
        if kind == 'bridges':
            geoms = geoms[shapely.length(geoms) >= MIN_BRIDGE_M]
        if len(geoms):
            merged = shapely.union_all(shapely.buffer(geoms, BUFFER_M, quad_segs=4))
            parts.append(gpd.GeoSeries([merged], crs=epsg).to_crs(epsg=4326).iloc[0])
    merged = shapely.union_all(parts) if parts else shapely.Polygon()
    polygons = [g for g in shapely.get_parts(merged) if not g.is_empty]
    return gpd.GeoDataFrame(geometry=polygons, crs='EPSG:4326')


def build(source, name, kind, target_dir, tmp, conf):
    """<target_dir>/<name>.gpkg (layer <kind>): filter (ogr2ogr), then simplify, length, buffer, merge in metres."""
    import geopandas as gpd
    work = os.path.join(tmp, f'{name}_{kind}.gpkg')
    if os.path.exists(work):
        os.remove(work)
    times = {}
    t = time.time()
    run(['ogr2ogr', '-f', 'GPKG', work, source, 'lines', '-oo', f'CONFIG_FILE={conf}', '-lco', 'GEOMETRY_NAME=geometry',
         '-nln', 'lines', '-where', f'{ROAD_FILTER} AND {TYPE_FILTER[kind]}'])
    times['lines'] = time.time() - t
    t = time.time()
    polygons = buffer_metric(gpd.read_file(work, layer='lines'), kind)
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, f'{name}.gpkg')
    if os.path.exists(target):
        os.remove(target)
    polygons.to_file(target, layer=kind, driver='GPKG', layer_options={'GEOMETRY_NAME': 'geometry'})
    times['buffer'] = time.time() - t
    os.remove(work)
    return target, times


def process_region(pbf, name, folders, tmp):
    """Tunnels and bridges of one downloaded region into folders {'tunnels': ..., 'bridges': ...}."""
    conf = osm_config(tmp)
    source, t_filter = prefilter(pbf, tmp)
    result = {'filter': t_filter}
    for kind in ('tunnels', 'bridges'):
        target, times = build(source, name, kind, folders[kind], tmp, conf)
        result.update({f'{kind}_{k}': v for k, v in times.items()})
    if source != pbf:
        os.remove(source)
    return result


def process_regions(rows, folders, tmp):
    """
    All rows (name_descr, name, url): download the next region while the current one is processed.
    """
    os.makedirs(tmp, exist_ok=True)
    slots = threading.Semaphore(2)          # At most two .pbf on disk: one processed, one downloaded
    ready = queue.Queue()

    def downloader():
        for row in rows:
            slots.acquire()
            try:
                path, seconds = download_pbf(row[2], tmp)
                ready.put((row, path, seconds, None))
            except Exception as e:
                ready.put((row, None, 0, e))
        ready.put(None)

    threading.Thread(target=downloader, daemon=True).start()
    started, done, timings, failed = time.time(), 0, {}, []
    while (item := ready.get()) is not None:
        row, path, seconds, error = item
        done += 1
        if error:
            log(f'[{done}/{len(rows)}] {row[0]}: download FAILED ({error})')
            failed.append(row[1])
            slots.release()
            continue
        t = time.time()
        try:
            steps = process_region(path, row[1], folders, tmp)
            timings[row[1]] = dict(download=seconds, size=os.path.getsize(path), **steps)
            log(f'[{done}/{len(rows)}] {row[0]}: {os.path.getsize(path) / 1e6:.0f} MB, download {seconds:.0f} s, '
                f'processing {time.time() - t:.0f} s  [{time.time() - started:.0f} s]')
        except Exception as e:
            log(f'[{done}/{len(rows)}] {row[0]}: processing FAILED ({e})')
            failed.append(row[1])
        finally:
            os.remove(path)
            slots.release()
    return timings, time.time() - started, failed



# --- Region outlines ---

def download_kml(url, path):
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    open(path, 'wb').write(r.content)


def outline(kml, target):
    """The region's outline: valid, merged, single polygons (Geofabrik's outlines are coarse: no simplification)."""
    name = os.path.basename(kml).replace('.kml', '')
    valid = kml.replace('.kml', '_valid.gpkg')
    for path in (valid, target):
        if os.path.exists(path):
            os.remove(path)
    run(['ogr2ogr', '-f', 'GPKG', valid, kml, '-makevalid', '-dialect', 'sqlite',
         '-sql', f"SELECT geometry FROM '{name}'", '-nln', 'outline', '-lco', 'GEOMETRY_NAME=geometry'])
    run(['ogr2ogr', '-f', 'GPKG', target, valid, '-dialect', 'sqlite', '-nln', 'outline', '-lco', 'GEOMETRY_NAME=geometry',
         '-sql', 'SELECT ST_Union(ST_Buffer(geometry, 0)) AS geometry FROM outline',
         '-explodecollections', '-nlt', 'POLYGON', '-a_srs', 'EPSG:4326'])
    os.remove(valid)


def outlines_from_kml(rows, output_folder, tmp):
    """Outlines of all rows (name_descr, name, url) into output_folder, a fresh staging folder; returns failed regions."""
    if os.path.lexists(output_folder):  # Leftovers of an aborted update
        shutil.rmtree(output_folder)
    os.makedirs(output_folder); os.makedirs(tmp, exist_ok=True)
    failed = []
    for n, row in enumerate(rows, 1):
        kml = os.path.join(tmp, f'{row[1]}.kml')
        try:
            download_kml(row[2].replace('-latest.osm.pbf', '.kml'), kml)
            outline(kml, os.path.join(output_folder, f'{row[1]}.gpkg'))
        except Exception as e:
            log(f'  Outline {row[0]} FAILED ({e})')
            failed.append(row[1])
        finally:
            if os.path.exists(kml):
                os.remove(kml)
        if n % 50 == 0 or n == len(rows):
            log(f'  {n} of {len(rows)} outlines')
    return failed


# --- Favourites: checked first by the app (find_osm_country goes through the outlines in name order) ---

FAVOURITES = ['switzerland', 'liechtenstein', 'germany', 'france', 'italy', 'austria', 'spain', 'canaryislands']


def prefix_favourites(folder):
    for n, name in enumerate(FAVOURITES, 1):
        path = os.path.join(folder, f'{name}.gpkg')
        if os.path.exists(path):
            os.rename(path, os.path.join(folder, f'{n}_{name}.gpkg'))
