import datetime
import functools
import glob
import os
import time
import zipfile

import numpy as np

import utilities


def is_update_in_progress(folder_prefix):
    """Check if an update is currently in progress"""
    lock_file = os.path.join(folder_prefix, 'GeoData/OSM/.update_in_progress.lock')

    if not os.path.exists(lock_file):
        return False

    # Check if lock is stale (older than 24 hours)
    try:
        with open(lock_file, 'r') as f:
            lines = f.readlines()
            if len(lines) >= 2:
                lock_time = float(lines[1].strip())
                if time.time() - lock_time > 86400:  # 24 hours
                    print('- Warning: Stale update lock detected (>24h old)')
                    return False
        return True
    except:
        return False

def safe_restore_from_archive(file_location, asset_type, folder_prefix):
    """
    Safely restore files from archive, but only if no update is in progress.
    Returns True if files are available (either already present or restored), False otherwise.
    """
    # Check if files already exist
    if len(glob.glob(os.path.join(file_location, '*MultiPoly.shp'))) > 0:
        return True

    # Check if update is in progress
    if is_update_in_progress(folder_prefix):
        print(f'- Update in progress, cannot restore {asset_type}. Please try again later.')
        return False

    # Proceed with restoration
    archive_path = os.path.join(folder_prefix, 'GeoData/OSM/archive', asset_type)
    if not os.path.exists(archive_path):
        print(f'- No archive found for {asset_type}')
        return False

    archives = glob.glob(os.path.join(archive_path, '*.zip'))
    latest_archive = max(archives, key=os.path.getmtime) if archives else None

    if latest_archive:
        print(f'- Restoring {asset_type} from archive: {os.path.basename(latest_archive)}')
        try:
            os.makedirs(file_location, exist_ok=True)
            with zipfile.ZipFile(latest_archive) as archive:
                for member in archive.infolist():
                    if not member.is_dir():  # Flat into file_location: older archives contain full paths
                        with archive.open(member) as source, open(os.path.join(file_location, os.path.basename(member.filename)), 'wb') as target:
                            target.write(source.read())
        except (OSError, zipfile.BadZipFile) as e:
            print(f'- Restoring {asset_type} failed: {e}')
            return False
        return len(glob.glob(os.path.join(file_location, '*MultiPoly.shp'))) > 0
    else:
        print(f'- No valid archive found for {asset_type}')
        return False

def __init__(parameters, assets):
    print('- Correcting Elevation of Tunnels and Bridges')
    find_osm_country(parameters, assets)
    assets = bridgeheightcorrection(parameters, assets)
    assets = tunnelheightcorrection(parameters, assets)
    return assets

@functools.lru_cache(maxsize=4)
def outline_bounds(outline_path, folder_mtime) -> dict:
    """
    Bounds (min lon, min lat, max lon, max lat) of every outline shapefile, read once per process. Cached per folder
    and its modification time (-> update safe).
    """
    import fiona
    bounds = {}
    for path in glob.glob(os.path.join(outline_path, '*MultiPoly.shp')):
        try:
            with fiona.open(path) as source:
                bounds[path] = tuple(source.bounds)
        except Exception:
            pass  # Unreadable: no bounds, so it is opened as before
    return bounds


def find_osm_country(parameters, assets):
    lat, lon = assets.track['lat'][0::5], assets.track['lon'][0::5]

    outline_path = os.path.join(parameters.folder_prefix, "GeoData/OSM/outlines")

    outline_path = os.path.realpath(outline_path)  # Version folder behind the symlink

    outlines = glob.glob(os.path.join(outline_path, '*MultiPoly.shp'))

    if len(outlines) == 0:
        # Try to restore, but respect update locks
        if not is_update_in_progress(parameters.folder_prefix):
            print('- Restoring outlines from archive')
            archive_dir = os.path.join(parameters.folder_prefix, 'GeoData/OSM/archive/outlines')
            if os.path.exists(archive_dir):
                safe_restore_from_archive(outline_path, 'outlines', parameters.folder_prefix)
                outlines = glob.glob(os.path.join(outline_path, '*MultiPoly.shp'))

        if len(outlines) == 0:
            print('- Warning: No outline files available')
            parameters.__setattr__('countries', [])
            return

    assigned = np.zeros(len(lat), dtype=bool)
    ctry = []
    bbox = utilities.bounding_box(lat, lon)  # Outlines away from the route are skipped while reading
    bounds = outline_bounds(outline_path, os.stat(outline_path).st_mtime)
    for outline in outlines:  # Until every point has been assigned to a country
        if assigned.all():
            break
        b = bounds.get(outline)
        if bbox and b and (b[2] < bbox[0] or b[0] > bbox[2] or b[3] < bbox[1] or b[1] > bbox[3]):
            continue  # Outline far from the route: not even opened
        inside = utilities.points_in_shapes(lat, lon, utilities.read_shapes(outline, bbox=bbox))
        if inside.any():
            assigned |= inside  # Distinct points: near borders a point lies within two (overlapping) outlines
            country = os.path.basename(outline).replace('_dissolved', '')
            if country not in ctry:
                ctry.append(country)
    parameters.__setattr__('countries', ctry)

def passage_mask(parameters, assets, asset_type):
    """
    Mask of track points within the OSM polygons of asset_type ('tunnels' or 'bridges') of the countries
    crossed. Returns (mask, file_location), mask is None if the OSM data is not available.
    """
    # Resolve symlink if it exists
    file_location = os.path.join(parameters.folder_prefix, f'GeoData/OSM/countries/{asset_type}')
    if os.path.islink(file_location):
        file_location = os.path.realpath(file_location)

    # Check if files exist, restore if needed (but respect update locks)
    if len(glob.glob(os.path.join(file_location, '*MultiPoly.shp'))) == 0:
        if not safe_restore_from_archive(file_location, asset_type, parameters.folder_prefix):
            print(f'- Warning: {asset_type.capitalize()} data not available, skipping {asset_type[:-1]} height correction')
            return None, file_location

    lat, lon = assets.track['lat'], assets.track['lon']
    mask = np.zeros(len(lat), dtype=bool)
    bbox = utilities.bounding_box(lat, lon)  # Only the polygons near the route are read

    for country in parameters.countries:
        country_file = os.path.join(file_location, country)
        if not os.path.exists(country_file):
            print(f'- {asset_type.capitalize()} for {country} could not be found')
        else:
            try:
                mask |= utilities.points_in_shapes(lat, lon, utilities.read_shapes(country_file, bbox=bbox))
            except Exception as e:
                print(f'- Error processing {asset_type} for {country}: {e}')

    # Close gaps: mark windows of 6 points with at least 4 hits
    for idx in range(3, len(mask) - 3, 4):
        if np.sum(mask[idx - 3:idx + 3]) >= 4:
            mask[idx - 3:idx + 3] = True
    return mask, file_location

def interpolate_elevation(assets, keep):
    """Replace the elevation of the points not in keep by linear interpolation (by point index)."""
    distance, elevation = assets.track['distance_m'], assets.track['elevation_m']
    interpvec_temp = np.linspace(distance[0], distance[-1], len(distance))
    assets.track.set(elevation_m=np.interp(interpvec_temp, interpvec_temp[keep], elevation[keep]))

def print_data_version(message, file_location):
    file_list = glob.glob(os.path.join(file_location, '*MultiPoly.shp'))
    if len(file_list) > 0:
        file_date = datetime.datetime.fromtimestamp(os.path.getmtime(file_list[0]))
        print(f'\t- {message} (V{file_date.strftime("%Y.%m")})')

def tunnelheightcorrection(parameters, assets):
    mask, file_location = passage_mask(parameters, assets, 'tunnels')
    if mask is None or not mask.any():
        return assets

    assets.track.set(in_tunnel=mask)  # E.g. for the sun and shade along the route (shade.py)
    print_data_version('Adjusting Elevation of Tunnel Passages', file_location)
    interpolate_elevation(assets, ~mask)

    # Length of the track segments with both ends within a tunnel
    tunnel_passages = np.flatnonzero(mask)
    delta_distances = np.insert(np.diff(assets.track['distance_m']), 0, 0)
    consecutive = np.insert(np.diff(tunnel_passages), 0, 0) == 1
    setattr(assets, 'tunnel_distance', np.sum(delta_distances[tunnel_passages[consecutive]]) / 1000)

    return assets

def bridgeheightcorrection(parameters, assets):
    mask, file_location = passage_mask(parameters, assets, 'bridges')
    if mask is None or not mask.any():
        return assets

    if not hasattr(assets, 'tunnel_distance'):  # If there were no tunnels detected before
        print_data_version('Adjusting Elevation of Bridge Passages', file_location)
    interpolate_elevation(assets, ~mask)

    return assets
