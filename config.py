"""Installation-specific settings (paths, publishing), replacing the former hostname checks.

Read from config.toml next to this file (see config.example.toml), each value overridable by an environment variable:

    data_dir     AERORACLE_DATA_DIR     Folder that contains GeoData/ (DEMs, OSM tunnels and bridges, gravimetry)
    temp_dir     AERORACLE_TEMP_DIR     Scratch folder for downloads (DEM tiles, OSM extracts in tools/osm)
    input_dir    AERORACLE_INPUT_DIR    Folder in which bare input file names (main.py -i route.tcx) are looked up
    publish_dir  AERORACLE_PUBLISH_DIR  Optional: main.py moves its output files here ...
    publish_url  AERORACLE_PUBLISH_URL  ... and prints download links below this URL (both needed for publishing)
    source_url   AERORACLE_SOURCE_URL   Optional: source code repository, linked in the web app's footer (AGPL section 13)
    whatif_pct   AERORACLE_WHATIF_PCT   Change of each parameter in the what-if table [%] (default 1)
    wind_shear_exponent  AERORACLE_WIND_SHEAR_EXPONENT  Effective exponent from 10 m wind to rider height (default 0.5, 0 = off)
    max_forecast_locations  AERORACLE_MAX_FORECAST_LOCATIONS  Weather forecast locations per ride at most (default 50)
    cornering    AERORACLE_CORNERING    Default rider level in corners: cautious, average, confident or racer (default average)
    max_pedalling_speed_kmh  AERORACLE_MAX_PEDALLING_SPEED_KMH  Speed above which riders stop pedalling (default 65)
"""
import os
import tempfile
import tomllib
from pathlib import Path

CONFIG_FILE = Path(__file__).with_name('config.toml')

_DEFAULTS = {
    'data_dir': None,
    'temp_dir': os.path.join(tempfile.gettempdir(), 'aeroracle'),
    'input_dir': os.path.join(tempfile.gettempdir(), 'tcx'),
    'publish_dir': None,
    'publish_url': None,
    'source_url': None,
    'whatif_pct': 1.0,
    'wind_shear_exponent': 0.5,
    'max_forecast_locations': 50,
    'cornering': 'average',
    'max_pedalling_speed_kmh': 65.0,
}


def _load():
    settings = dict(_DEFAULTS)
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'rb') as f:
            settings.update({key: value for key, value in tomllib.load(f).items() if key in _DEFAULTS and value != ''})
    for key in _DEFAULTS:
        if os.environ.get(f'AERORACLE_{key.upper()}'):
            settings[key] = os.environ[f'AERORACLE_{key.upper()}']
    return settings


_settings = _load()
DATA_DIR: str | None = _settings['data_dir']
TEMP_DIR: str = _settings['temp_dir']
INPUT_DIR: str = _settings['input_dir']
PUBLISH_DIR: str | None = _settings['publish_dir']
PUBLISH_URL: str | None = _settings['publish_url'].rstrip('/') if _settings['publish_url'] else None
PUBLISHING = bool(PUBLISH_DIR and PUBLISH_URL)
SOURCE_URL: str | None = _settings['source_url']
WHATIF_PCT: float = abs(float(_settings['whatif_pct']))
WIND_SHEAR_EXPONENT: float = abs(float(_settings['wind_shear_exponent']))
MAX_FORECAST_LOCATIONS: int = max(2, int(_settings['max_forecast_locations']))
CORNERING: str = _settings['cornering']
MAX_PEDALLING_SPEED_KMH: float = float(_settings['max_pedalling_speed_kmh'])
