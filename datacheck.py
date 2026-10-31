"""Report which data sets, credentials and external programs are available (main.py --check-data).

Every data set is optional: without it, AerOracle falls back to the next elevation model (finally SRTM, downloaded
automatically) or skips the correction (tunnels, bridges, gravity anomaly). See README.md, "Data".
"""
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import config


@dataclass(frozen=True)
class DataSet:
    name: str
    files: tuple[str, ...]  # Paths below GeoData/, glob patterns allowed; all needed
    obtained: str           # How the files get there
    missing: str            # Effect if not available


DATA_SETS = (
    DataSet('swissALTI3D (Switzerland, 5 m)',
            ('DEM/swisstopo/swissALTI3D_5m_elevation_r2023_10xuint16_*.tif',  # _COG.tif or _compressed_DEFLATE.tif
             'DEM/swisstopo/swissBOUNDARIES3D_1_3_TLM_LANDESGEBIET_Simplified.shp'),
            'manual', 'other elevation models used in Switzerland'),
    DataSet('Sonny LiDAR DTMs (parts of Europe)',
            ('DEM/SonnyDEM/SonnyDEM_Tiles_Outline_Singlepart.shp', 'DEM/SonnyDEM/*.tif'),
            'manual', 'other elevation models used'),
    DataSet('Copernicus DEM GLO-30', ('DEM/CopernicusDEM/*_10xInt32.tif',),
            'downloaded when needed (needs gdal_calc.py)', 'ALOS, EU-DEM, ASTER or SRTM used'),
    DataSet('ALOS AW3D30', ('DEM/ALOS/ALPSMLC30_*_DSM.tif',),
            'downloaded when needed', 'EU-DEM, ASTER or SRTM used'),
    DataSet('EU-DEM outline', ('DEM/EUDEM/EUDEM_Boundaries_Singleparts.shp',),
            'manual (tiles then downloaded when needed)', 'EU-DEM not used'),
    DataSet('ASTER GDEM V003', ('DEM/ASTER/*_dem.tif',),
            'downloaded when needed (needs USGS credentials)', 'SRTM used'),
    DataSet('SRTM', ('DEM/SRTM3/*.hgt',), 'downloaded when needed', 'no elevation where no other model covers'),
    DataSet('World Gravity Map WGM2012', ('Gravimetry/WGM2012_Freeair_ponc_2min_mgal_Int16_LZW.tif',),
            'manual', 'normal gravity without local anomaly (negligible)'),
    DataSet('OSM tunnels', ('OSM/countries/tunnels/*_MultiPoly.shp',),
            'tools/osm/osm_tunnels_world.py', 'no elevation correction in tunnels'),
    DataSet('OSM bridges', ('OSM/countries/bridges/*_MultiPoly.shp',),
            'tools/osm/osm_tunnels_world.py', 'no elevation correction on bridges'),
    DataSet('OSM country outlines', ('OSM/outlines/*_MultiPoly.shp',),
            'tools/osm/osm_tunnels_world.py', 'no tunnel and bridge corrections'),
)

# credentials.json entry -> what it is needed for
CREDENTIALS = {
    'RWGPS': 'RideWithGPS links (username: API key, password: auth token)',
    'USGS': 'ASTER GDEM downloads (NASA Earthdata login)',
    'Meteoblue': 'meteoblue forecasts (API key as password)',
    'VisualCrossing': 'Visual Crossing forecasts (API key as password)',
}

# External program -> what it is needed for
PROGRAMS = {
    'gpsbabel': 'GPX and FIT output',
    'gdal_calc.py': 'Copernicus DEM downloads',
    'osmconvert': 'tools/osm (OSM extracts)',
    'osmfilter': 'tools/osm (OSM extracts)',
    'ogr2ogr': 'tools/osm (shapefiles)',
}


def size_text(size):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024


def matches(geodata, pattern):
    return sorted(path for path in geodata.glob(pattern) if path.is_file())


def report():
    """Print the availability of data, credentials and programs. Returns False if no data folder is configured."""
    print('Data folder')
    if not config.DATA_DIR:
        print('  not configured: set data_dir in config.toml (see config.example.toml) or AERORACLE_DATA_DIR')
        return False
    geodata = Path(config.DATA_DIR) / 'GeoData'
    print(f'  {geodata}{"" if geodata.is_dir() else "  (does not exist yet: created by the first download)"}')

    print('\nData sets')
    for data_set in DATA_SETS:
        found = [matches(geodata, pattern) for pattern in data_set.files]
        if all(found):
            files = [path for paths in found for path in paths]
            count = f'{len(files)} files' if len(files) > len(data_set.files) else ''
            details = ', '.join(filter(None, (count, size_text(sum(path.stat().st_size for path in files)))))
            print(f'  [ok     ] {data_set.name}: {details}')
        elif data_set.obtained.startswith('downloaded'):
            needs = data_set.obtained.removeprefix('downloaded when needed').strip(' ()')
            print(f'  [no tile] {data_set.name}: none yet, downloaded when a route needs it{f" ({needs})" if needs else ""}')
        else:
            absent = ', '.join(pattern for pattern, paths in zip(data_set.files, found) if not paths)
            print(f'  [missing] {data_set.name}: {absent}')
            print(f'            source: {data_set.obtained}; without it: {data_set.missing}')

    print('\nCredentials (credentials.json)')
    credentials_file = Path(__file__).with_name('credentials.json')
    try:
        credentials = json.loads(credentials_file.read_text())
    except (OSError, ValueError):
        credentials = {}
        print('  credentials.json not found or not readable (template: credentials.example.json)')
    for key, purpose in CREDENTIALS.items():
        entry = credentials.get(key) or {}
        print(f'  [{"ok" if entry.get("password") else "missing":7s}] {key}: {purpose}')

    print('\nPrograms')
    for program, purpose in PROGRAMS.items():
        print(f'  [{"ok" if shutil.which(program) else "missing":7s}] {program}: {purpose}')

    print('\nPublishing of main.py outputs: '
          + (f'{config.PUBLISH_DIR} -> {config.PUBLISH_URL}' if config.PUBLISHING else 'off (outputs stay next to the input)'))
    if config.PUBLISHING and not os.access(config.PUBLISH_DIR, os.W_OK):
        print(f'  warning: {config.PUBLISH_DIR} is not writable')
    return True
