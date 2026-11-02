"""
Sun or terrain shadow along the route at the predicted passing times.

For points every STATION_SPACING_M along the route: the sun's position at the time the rider passes, and the terrain's
horizon in the sun's direction (rays from NEAR_FIELD_M out to RAY_REACH_M over the local DEMs, with Earth curvature and
refraction). Per track point the result is a state (SUN, SHADE, TUNNEL, NIGHT or UNKNOWN), stored as track['sun_state'].

Heights along the rays come from the local DEM tiles only (no downloads), per 1x1 deg cell from the first source that has
it, in the order of dem.py: rays run across tile edges and fill gaps of one source with the next. A cell without any
local tile is sea if Copernicus GLO-30 has no tile there (its tile list is fetched once); a land cell without local data
makes the horizon UNKNOWN where a high peak there could hide the sun.
"""
import functools
import glob
import os

import numpy as np
import requests

import config
import dem
import utilities

SUN, SHADE, TUNNEL, NIGHT, UNKNOWN = range(5)
STATE_NAMES = ['Unshaded', 'Shaded', 'Tunnel', 'Night', 'Horizon unknown']  # By the terrain: trees, buildings and clouds not included

STATION_SPACING_M = 100      # Sun and horizon every 100 m along the route
NEAR_FIELD_M = 60            # Terrain closer than this is ignored: 30 m DEMs cannot resolve the road's cut into a slope
RAY_REACH_M = 100_000        # Far enough for low winter sun in the Alps (3,000 m higher at 3 deg: 57 km)
RAY_SAMPLES = 200            # Geometric spacing: dense near the road, sparse far away
MIN_SHADE_M = 300            # Shorter stretches of shade (or of sun between shade) are dropped as noise
RIDER_HEIGHT_M = 1.2
EARTH_RADIUS_M = 6_371_000
REFRACTION_K = 0.13          # Standard terrestrial refraction coefficient
HIGHEST_PEAK_M = 4_800       # Upper bound for terrain in a cell without data (Mont Blanc)
SUNSET_DEG = -0.833          # Sun's centre at sunrise/sunset (refraction and the sun's radius)
COPERNICUS_TILE_LIST_URL = 'https://copernicus-dem-30m.s3.amazonaws.com/tileList.txt'  # Land cells (sea test)


def sun_position(timestamp, lat, lon):
    """Elevation and azimuth of the sun [deg] at Unix timestamps (NOAA formulas, about 0.1 deg; no refraction)."""
    n = np.asarray(timestamp, float) / 86400 + 2440587.5 - 2451545.0  # Days since J2000
    mean_longitude = np.radians((280.460 + 0.9856474 * n) % 360)
    anomaly = np.radians((357.528 + 0.9856003 * n) % 360)
    ecliptic_longitude = mean_longitude + np.radians(1.915) * np.sin(anomaly) + np.radians(0.020) * np.sin(2 * anomaly)
    obliquity = np.radians(23.439 - 4e-7 * n)
    declination = np.arcsin(np.sin(obliquity) * np.sin(ecliptic_longitude))
    right_ascension = np.arctan2(np.cos(obliquity) * np.sin(ecliptic_longitude), np.cos(ecliptic_longitude))
    hour_angle = np.radians((280.46061837 + 360.98564736629 * n) % 360 + np.asarray(lon, float)) - right_ascension
    phi = np.radians(np.asarray(lat, float))
    elevation = np.arcsin(np.sin(phi) * np.sin(declination) + np.cos(phi) * np.cos(declination) * np.cos(hour_angle))
    azimuth = np.arctan2(-np.sin(hour_angle), np.tan(declination) * np.cos(phi) - np.sin(phi) * np.cos(hour_angle))
    return np.degrees(elevation), np.degrees(azimuth) % 360


def refraction_deg(elevation):
    """Atmospheric refraction [deg] at a geometric elevation (Saemundsson), 0 well below the horizon."""
    e = np.asarray(elevation, float)
    r = 1.02 / np.tan(np.radians(e + 10.3 / (e + 5.11))) / 60
    return np.where(e > -1.5, np.clip(r, 0, 0.6), 0.0)


def cell_name(i, j, digits_lat=2):
    return f"{'N' if i >= 0 else 'S'}{abs(i):0{digits_lat}d}{'E' if j >= 0 else 'W'}{abs(j):03d}"


def cell_tiles(folder_prefix, i, j):
    """Local DEM tiles of the 1x1 deg cell (lower left corner lat i, lon j) in dem.py's order: (path, scale)."""
    base = f'{folder_prefix}/GeoData/DEM'
    lat_p, lon_p = ('N' if i >= 0 else 'S'), ('E' if j >= 0 else 'W')
    candidates = [
        (f'{base}/SonnyDEM/{cell_name(i, j)}.tif', 1),
        (f'{base}/CopernicusDEM/Copernicus_DSM_COG_10_{lat_p}{abs(i):02d}_00_{lon_p}{abs(j):03d}_00_DEM_10xInt32.tif', 0.1),
        (f'{base}/ALOS/ALPSMLC30_{cell_name(i, j, 3)}_DSM.tif', 1),
        (f'{base}/ASTER/ASTGTMV003_{cell_name(i, j)}_dem.tif', 1),
        (f'{base}/SRTM3/{cell_name(i, j)}.hgt', 1),
    ]
    return [(path, scale) for path, scale in candidates if os.path.exists(path)]


def swiss_mosaic(folder_prefix):
    folder = f'{folder_prefix}/GeoData/DEM/swisstopo'
    for name in ('swissALTI3D_5m_elevation_r2023_10xuint16_COG.tif', 'swissALTI3D_5m_elevation_r2023_10xuint16_compressed_DEFLATE.tif'):
        if os.path.exists(f'{folder}/{name}'):
            return f'{folder}/{name}'
    return None


@functools.lru_cache(maxsize=1)
def copernicus_land_cells():
    """(lat, lon) of all 1x1 deg cells with land: the tiles of Copernicus GLO-30 (list cached in temp_dir); None if unavailable."""
    path = os.path.join(config.TEMP_DIR, 'copernicus_tileList.txt')
    try:
        if not os.path.exists(path):
            response = requests.get(COPERNICUS_TILE_LIST_URL, timeout=30)
            response.raise_for_status()
            os.makedirs(config.TEMP_DIR, exist_ok=True)
            with open(path, 'w') as f:
                f.write(response.text)
        cells = set()
        for name in open(path).read().split():
            parts = name.split('_')  # Copernicus_DSM_COG_10_N46_00_E007_00_DEM
            lat, lon = int(parts[4][1:]) * (1 if parts[4][0] == 'N' else -1), int(parts[6][1:]) * (1 if parts[6][0] == 'E' else -1)
            cells.add((lat, lon))
        return cells or None
    except Exception:
        return None


@functools.lru_cache(maxsize=1024)
def cell_is_sea(folder_prefix, i, j):
    """True for open sea: no Copernicus GLO-30 tile (else, without its tile list: no OSM region outline touches the cell)."""
    land = copernicus_land_cells()
    if land is not None:
        return (i, j) not in land
    outlines = glob.glob(os.path.join(os.path.realpath(f'{folder_prefix}/GeoData/OSM/outlines'), '*MultiPoly.shp'))
    if not outlines:
        return False  # Unknown: rather report the horizon as unknown
    bbox = (float(j), float(i), float(j + 1), float(i + 1))
    return not any(utilities.read_shapes(outline, bbox=bbox) for outline in outlines)


def terrain_heights(lat, lon, folder_prefix):
    """
    Terrain height [m] at the points from the local DEMs; NaN at sea or without data.
    Also returns a mask of the points lying in land cells without any local tile (heights unknown).
    """
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    heights = np.full(lat.shape, np.nan)
    mosaic = swiss_mosaic(folder_prefix)
    if mosaic:  # swissALTI3D first, as in dem.py: values x10, 0 outside Switzerland
        east, north = dem.wgs84_to_lv95(lat, lon)
        inside = (east > 2_480_000) & (east < 2_840_000) & (north > 1_070_000) & (north < 1_300_000)
        if inside.any():
            values = utilities.getpixelvalues(north[inside], east[inside], mosaic) / 10
            heights[inside] = np.where(values > 0, values, np.nan)
    missing_data = np.zeros(lat.shape, bool)
    cell_lat, cell_lon = np.floor(lat).astype(int), np.floor(lon).astype(int)
    for i, j in {(a, b) for a, b in zip(cell_lat[np.isnan(heights)], cell_lon[np.isnan(heights)])}:
        in_cell = (cell_lat == i) & (cell_lon == j)
        tiles = cell_tiles(folder_prefix, i, j)
        for path, scale in tiles:
            todo = in_cell & np.isnan(heights)
            if not todo.any():
                break
            values = utilities.getpixelvalues(lat[todo], lon[todo], path) * scale
            heights[todo] = np.where(values > -500, values, np.nan)  # No-data values of all sources are below -500
        if not tiles and not cell_is_sea(folder_prefix, i, j):
            missing_data |= in_cell
    return heights, missing_data


def stretches(mask):
    """(start, end) index pairs of the runs of True in mask, end exclusive."""
    edges = np.flatnonzero(np.diff(np.r_[0, np.asarray(mask, int), 0]))
    return list(zip(edges[::2], edges[1::2]))


def station_states(lat, lon, utc, observer_m, folder_prefix):
    """State (SUN, SHADE, NIGHT, UNKNOWN) and apparent sun elevation [deg] at the stations."""
    geometric, azimuth = sun_position(utc, lat, lon)
    elevation = geometric + refraction_deg(geometric)
    state = np.full(len(elevation), NIGHT)
    day = elevation > SUNSET_DEG  # Rays towards the sun only while it is up
    if not day.any():
        return state, elevation
    lat, lon, azimuth, observer_m, sun = lat[day], lon[day], azimuth[day], observer_m[day], elevation[day]
    steps = np.geomspace(NEAR_FIELD_M, RAY_REACH_M, RAY_SAMPLES)
    north, east = np.cos(np.radians(azimuth))[:, None] * steps, np.sin(np.radians(azimuth))[:, None] * steps
    ray_lat = lat[:, None] + north / 111_320
    ray_lon = lon[:, None] + east / (111_320 * np.cos(np.radians(lat))[:, None])
    heights, no_data = terrain_heights(ray_lat.ravel(), ray_lon.ravel(), folder_prefix)
    heights, no_data = heights.reshape(ray_lat.shape), no_data.reshape(ray_lat.shape)
    drop = steps ** 2 / (2 * EARTH_RADIUS_M) * (1 - REFRACTION_K)  # Earth curvature, less refraction
    angle = np.degrees(np.arctan2(heights - drop - observer_m[:, None], steps))
    horizon = np.nanmax(np.where(np.isnan(angle), -90.0, angle), axis=1)
    # Land without data: unknown where even the highest peak there could hide the sun
    worst = np.degrees(np.arctan2(HIGHEST_PEAK_M - drop - observer_m[:, None], steps))
    blind = np.any(no_data & (worst > sun[:, None]), axis=1)
    state[day] = np.where(horizon > sun, SHADE, np.where(blind, UNKNOWN, SUN))
    return state, elevation


def add_sun_state(parameters, assets):
    """Set track['sun_state'] (needs the start time and the predicted timestamps)."""
    track = assets.track
    if not parameters.time or len(track) < 2:
        return
    distance = track['distance_m']
    stations = np.arange(0, distance[-1] + STATION_SPACING_M, STATION_SPACING_M)
    stations = np.minimum(stations, distance[-1])
    lat, lon = np.interp(stations, distance, track['lat']), np.interp(stations, distance, track['lon'])
    timestamp = track['timestamp']
    utc = parameters.time + np.interp(stations, distance, timestamp - timestamp[0])  # The timestamps are shifted to local time
    observer = np.interp(stations, distance, np.nan_to_num(track['elevation_m'])) + RIDER_HEIGHT_M
    state, elevation = station_states(lat, lon, utc, observer, parameters.folder_prefix)

    min_stations = int(np.ceil(MIN_SHADE_M / STATION_SPACING_M))
    for a, b in stretches(state == SHADE):  # Short shade: noise of the DEM next to the road
        if b - a < min_stations:
            state[a:b] = SUN
    for a, b in stretches(state == SUN):  # Short sun between shade
        if b - a < min_stations and a > 0 and b < len(state) and state[a - 1] == SHADE and state[b] == SHADE:
            state[a:b] = SHADE

    nearest = np.clip(np.round(distance / STATION_SPACING_M).astype(int), 0, len(stations) - 1)
    point_state = state[nearest]
    if track.has('in_tunnel'):
        point_state = np.where(track['in_tunnel'], TUNNEL, point_state)
    track.set(sun_state=point_state.astype(np.int8))


def summary(track):
    """Distance [km] and riding time [s] per state, None without sun states."""
    if not track.has('sun_state'):
        return None
    state = track['sun_state'][1:]
    step = np.diff(track['distance_m'])
    seconds = np.diff(track['timestamp'])
    return {name: (float(step[state == k].sum() / 1000), float(seconds[state == k].sum())) for k, name in enumerate(STATE_NAMES)}
