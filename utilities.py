import os
import shutil
import socket
import sys
import tarfile
import zipfile
from datetime import datetime
import urllib.request
import numpy as np
import polars as pl
import rasterio
import re


def tcx_download(parameters):
    if not check_internet_connection():
        display_error_message('No internet connection - cannot download file. Exiting.')
        sys.exit()
    else:
        print(f'- Downloading file {os.path.split(parameters.input)[-1]}')
        cmd_output = os.system(f"wget -O {parameters.input} {parameters.url} >/dev/null 2>&1")
        if cmd_output!=0:
            print(f'\t- Download failed. Please check URL entered ({parameters.url}). Exiting.')
            sys.exit()
            
def parse_rwgps_url(url):
    """
    Parse a RideWithGPS route/trip URL into its components.
    Returns (asset_type, asset_id, privacy_code).
    """
    from urllib.parse import urlparse, parse_qs

    match = re.search(r'ridewithgps\.com/(routes|trips)/(\d+)', url)
    if not match:
        raise ValueError(f"Could not parse RideWithGPS asset type/id from URL: {url}")

    asset_type = match.group(1)
    asset_id = match.group(2)

    query = parse_qs(urlparse(url).query)
    privacy_code = query.get('privacy_code', [None])[0]

    return asset_type, asset_id, privacy_code


def rwgps_download(parameters):
    """
    Fetch a route or trip from the RideWithGPS API as JSON and write it to
    parameters.input (expected to be a .json path).
    Expects parameters.url to hold the RideWithGPS route/trip URL.
    """
    import json
    import requests

    if not check_internet_connection():
        display_error_message('No internet connection - cannot download file. Exiting.')
        sys.exit()

    api_key, auth_token = get_credentials('RWGPS')
    if not api_key or not auth_token:
        display_error_message(
            'RideWithGPS API credentials missing. Add a "RWGPS" entry '
            '(username=api_key, password=auth_token) to credentials.json.'
        )
        sys.exit()

    try:
        asset_type, asset_id, privacy_code = parse_rwgps_url(parameters.url)
    except ValueError as e:
        display_error_message(str(e))
        sys.exit()

    print(f'- Downloading {asset_type[:-1]} {asset_id} from RideWithGPS API')

    api_url = f"https://ridewithgps.com/api/v1/{asset_type}/{asset_id}.json"
    headers = {
        'x-rwgps-api-key': api_key,
        'x-rwgps-auth-token': auth_token,
    }
    params = {}
    if privacy_code:
        params['privacy_code'] = privacy_code

    try:
        response = requests.get(api_url, headers=headers, params=params, timeout=30)
    except requests.exceptions.RequestException as e:
        display_error_message(f'Network error while downloading from RideWithGPS: {e}. Exiting.')
        sys.exit()

    if response.status_code == 200:
        target = getattr(parameters, 'rwgps_json', None) or parameters.input
        with open(target, 'w') as f:
            json.dump(response.json(), f)
    elif response.status_code == 401:
        display_error_message('RideWithGPS: unauthorized - invalid API key or auth token. Exiting.')
        sys.exit()
    elif response.status_code == 403:
        display_error_message(f'RideWithGPS: {asset_type[:-1]} not visible to user (private asset, wrong/missing privacy_code?). Exiting.')
        sys.exit()
    elif response.status_code == 404:
        display_error_message(f'RideWithGPS: {asset_type[:-1]} {asset_id} not found. Exiting.')
        sys.exit()
    else:
        display_error_message(f'RideWithGPS: unexpected error (HTTP {response.status_code}). Exiting.')
        sys.exit()

def check_internet_connection():
    try:
        # connect to the host -- tells us if the host is actually
        # reachable
        socket.create_connection(("www.google.com", 80))
        return True
    except OSError:
        pass
        return False

# Function to check the presence of a requested file on the server (e.g. DEM tile)
def check_file(url, timeout=5):
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False

def display_error_message(errorMessage):
    print(f"!!! {'=' * (len(errorMessage) - 8)} !!!")
    print(errorMessage)
    print('=' * len(errorMessage))


def extract_specific_files(input_path, target_folder, file_ending):
    if '.zip' in os.path.basename(input_path) or '.ZIP' in os.path.basename(input_path):
        with zipfile.ZipFile(input_path, 'r') as zip_ref:
            for file in zip_ref.namelist():
                if file.endswith(file_ending):
                    # Extract the file to a temporary path
                    temp_path = os.path.join(target_folder, file)
                    zip_ref.extract(file, target_folder)
                    # Move the file to the target directory, without subdirectories
                    os.rename(temp_path, os.path.join(target_folder, os.path.basename(file)))
    elif '.tar' in os.path.basename(input_path) or '.TAR' in os.path.basename(input_path):
        with tarfile.open(input_path) as tar:
            for member in tar.getmembers():
                if member.name.endswith(file_ending):
                    # Extract the file without the directory structure
                    tar.extract(member, target_folder)
                    if member.isdir():
                        # Skip directories
                        continue
                    # Move the file to the path_to_extract
                    basename = os.path.basename(member.name)
                    os.rename(os.path.join(target_folder, member.name), os.path.join(target_folder, basename))


def get_credentials(tag):
    import json
    with open(os.path.join(os.path.dirname(__file__),'credentials.json')) as f:
        credentials = json.load(f)
    try:
        username = credentials[tag]['username']
    except:
        username = None
    try:
        password = credentials[tag]['password']
    except:
        password = None

    return username, password

def get_version():
    import json
    with open(os.path.join(os.path.dirname(__file__),'version.json')) as f:
        credentials = json.load(f)
    try:
        version_backend = credentials["version_backend"]["number"]
        version_frontend = credentials["version_frontend"]["number"]
    except:
        version_backend, version_frontend = None, None

    return version_backend, version_frontend

def get_osm_data_date():
    """Return the OSM data date (YYYY-MM) written by tools/osm/osm_tunnels_world.py, or None if unavailable."""
    import json
    import config
    try:
        path = os.path.join(config.DATA_DIR, 'GeoData/OSM/countries/tunnels/osm_data_date.json')
        with open(path) as f:
            return json.load(f)['osm_data_date']
    except Exception:
        return None

WEATHER_PROVIDER_NAMES = {'o': 'Open-Meteo', 'v': 'VisualCrossing', 'm': 'meteoblue'}


def time_weighted_mean(track, values):
    """Mean of a per-point quantity, weighted by the time spent on each segment."""
    timestamp = track['timestamp']
    return np.nansum(np.diff(timestamp, prepend=timestamp[0]) * values) / (timestamp[-1] - timestamp[0])


def meteo_chart_path(route_path):
    """Wind chart written next to the route's output files."""
    return os.path.splitext(route_path)[0] + '_meteo.jpeg'


def plot_wind_conditions(parameters, assets):
    import tilemapbase
    from mpl_toolkits.axes_grid1.inset_locator import inset_axes
    from matplotlib.patches import Rectangle
    from matplotlib.collections import LineCollection
    from matplotlib.colors import Normalize
    import matplotlib.pyplot as plt

    print("- Plotting Weather Data")
    track = assets.track
    lat, lon, distance_km = track['lat'], track['lon'], track['distance_m'] / 1000
    elevation, timestamp, headwind = track['elevation_m'], track['timestamp'], track['headwind_ms']
    temperature, is_forecast_point = track['temperature_c'], track['is_forecast_point']

    plt.figure(figsize=(25, 15))
    grid = plt.GridSpec(10, 16, hspace=4)
    ax1 = plt.subplot(grid[:7, :11])
    if check_internet_connection():
        tilemapbase.init(create=True)
        t = tilemapbase.tiles.build_OSM()
        buffer_dist = 5000 / (111320 * np.cos(np.nanmean(lat) * (np.pi / 180))) # Calculate 5000 m in °
        extent = tilemapbase.Extent.from_lonlat(np.nanmin(lon)-buffer_dist, np.nanmax(lon)+buffer_dist,
                                                np.nanmin(lat)-buffer_dist, np.nanmax(lat)+buffer_dist)
        extent = extent.to_aspect(ax1.get_window_extent().width/ax1.get_window_extent().height, False)
        plotter = tilemapbase.Plotter(extent, t, width=600)
        plotter.plot(ax1, t)
        path = [tilemapbase.project(x, y) for x, y in zip(lon, lat)]
        x, y = zip(*path)
        cax_lims = np.nanmax([4, np.abs(np.percentile(headwind, 0.05)), np.abs(np.percentile(headwind, 0.95))])

        # Create line segments for LineCollection
        points = np.array([x, y]).T.reshape(-1, 1, 2)
        segments = np.concatenate([points[:-1], points[1:]], axis=1)

        # Create LineCollection with colors based on wind speed
        norm = Normalize(vmin=-1*np.round(cax_lims)-0.5, vmax=np.round(cax_lims)+0.5)
        lc = LineCollection(segments, cmap=plt.cm.RdYlGn_r, norm=norm, linewidth=7, capstyle='round', joinstyle='round')
        lc.set_array(headwind)
        cax = ax1.add_collection(lc)

        ax1.xaxis.set_visible(False)
        ax1.yaxis.set_visible(False)
    else:
        points = np.array([lon, lat]).T.reshape(-1, 1, 2)
        segments = np.concatenate([points[:-1], points[1:]], axis=1)

        norm = Normalize(vmin=-5, vmax=5)
        lc = LineCollection(segments, cmap=plt.cm.RdYlGn_r, norm=norm, linewidth=7, capstyle='round', joinstyle='round')
        lc.set_array(headwind)
        cax = ax1.add_collection(lc)
        ax1.autoscale()

        ax1.set_xlabel('Longitude [°]', fontsize=16)
        ax1.set_ylabel('Latitude [°]', fontsize=16)
    colorbar_anchor = (.90, 0., 1, 1)  # x0, y0, width, height in ax1 coordinates
    axins = inset_axes(ax1,
                       width="5%",  # width = 5% of parent_bbox width
                       height="100%",  # height : 100%
                       loc='lower left',
                       bbox_to_anchor=colorbar_anchor,
                       bbox_transform=ax1.transAxes,
                       borderpad=0,
                       )

    cb = plt.colorbar(cax, cax=axins)
    # White background from the left edge of the colorbar to the right limit of ax1
    rectangle = Rectangle(colorbar_anchor[:2], colorbar_anchor[2], colorbar_anchor[3], transform=ax1.transAxes)
    ax1.add_patch(rectangle)
    rectangle.set_facecolor('white')
    cb.ax.set_ylabel('Tail wind     [m/s]     Head wind', fontsize=16)

    ax2 = plt.subplot(grid[7:, :])
    ax2.plot(distance_km, temperature, 'b')
    ax2.plot(distance_km[is_forecast_point], temperature[is_forecast_point], 'bx')
    ax2.set_xlabel('Distance [km]', fontsize=16)
    ax2.set_ylabel('Temperature [°C]', fontsize=16, color='b')
    ax3 = ax2.twinx()
    ax3.set_ylabel('Elevation [masl]', fontsize=16, color='k')
    ele_limit = np.nanmin(elevation)-(np.nanmax(elevation)-np.nanmin(elevation))*.075
    mask = elevation > ele_limit
    ax3.fill_between(distance_km, elevation, ele_limit, where=mask, color='black', alpha=0.075)
    ax3.plot(distance_km, elevation, 'k')

    ride_time = timestamp[-1] - timestamp[0]
    multiplier = np.floor(ride_time/(3600*6))+1 # check length of ride (increase time line distance by 30' for each 6 hours of riding time
    timestamp_interval = (timestamp - timestamp[0]) % (1800*multiplier)
    timestep = 1800*multiplier

    timestamp_idx = np.where(np.diff(timestamp_interval) < 0)[0] + 1
    for i, idx in enumerate(timestamp_idx):
        hours, remainder = divmod((i + 1) * timestep, 3600)
        minutes, seconds = divmod(remainder, 60)
        ax3.plot([distance_km[idx], distance_km[idx]], [ele_limit, np.nanmax(elevation)*0.85], ':k')
        ax3.text(distance_km[idx], np.nanmax(elevation)*0.9, '{:02}:{:02}:{:02}'.format(int(hours), int(minutes), int(seconds)), rotation=45, va='center', ha='center', backgroundcolor=[1, 1, 1, 0.6])
    plt.draw()

    # Textbox
    height_diff = np.diff(elevation)
    hours = ride_time / 3600
    minutes = 60 * (hours % 1)
    seconds = 60 * (minutes % 1)
    wind_mean_time = np.round(time_weighted_mean(track, headwind) * 10) / 10
    yaw_angle_mean_time = np.round(time_weighted_mean(track, track['yaw_angle_deg']) * 10) / 10

    temp_range = [np.nanmin(temperature), np.nanmax(temperature)]
    weather_api = WEATHER_PROVIDER_NAMES[parameters.weather]
    weather = assets.weather_summary
    if weather.sunshine_fraction is not None and weather.uv_index_max is not None:
        sun_string = f'{int(np.round(weather.sunshine_fraction * 100))}% (Max. UV: {weather.uv_index_max:.1f})'
        sun_info = True
    elif weather.sunshine_fraction is not None:
        sun_string = f'{int(np.round(weather.sunshine_fraction * 100))}%'
        sun_info = True
    else:
        sun_info = False
    precipitation_info = weather.precipitation_mm is not None

    textstr_left = '\n'.join((
        f'Ride Information',
        f'=================================',
        f'Distance:',
        f'Riding Time:',
        f'Average Speed:',
        f'Height Difference:',
        f'Resulting Power:',
        f'Estimated kcal Burned:',
        f'',
        f'',
        f'Weather Information',
        f'=================================',
        f'Min/Max Temperature:',
        f'Mean Inline Wind Speed:',
        *(f'Sunshine Duration:' for _ in (1,) if sun_info), # Only print if sunshine_fraction present
        *(f'Total Precipitation:' for _ in (1,) if precipitation_info),  # Only print if precipitation_mm present
        f'',
        f'',
        f'Number for Nerds',
        f'=================================',
        f'Mean Yaw Angle:',
        f'',
        f'',
        f'Calculation Parameters',
        f'=================================',
        f'System Weight:',
        f'Power (Flat/Up/Down):',
        f'CdA (Flat/Up/Down):',
        f'Start Time:',
        f'Model Run:'
    ))

    textstr_right = '\n'.join((
        '',
        '',
        f'{np.floor(100 * distance_km[-1]) / 100:.2f} km',
        f'{int(np.floor(hours)):02d}:{int(np.floor(minutes)):02d}:{int(np.floor(seconds)):02d}',
        f'{np.round(100 * (distance_km[-1] * 1000 / ride_time * 3.6)) / 100:.2f} km/h',
        f'+{int(np.sum(height_diff[height_diff > 0])):d} m/{int(np.sum(height_diff[height_diff < 0])):d} m',
        f'Avg {parameters.power_out.average_w:.0f} W | NP {parameters.power_out.normalized_w:.0f} W',
        f'{(parameters.power_out.average_w * ride_time) / 1000:.0f} kcal',
        f'',
        f'',
        f'',
        f'',
        f'{temp_range[0]:0.1f} – {temp_range[1]:0.1f} °C',
        f'+{wind_mean_time:0.1f} m/s (Head Wind)' if wind_mean_time > 0 else (f'{wind_mean_time:0.1f} m/s (Tail Wind)' if wind_mean_time < 0 else f'{np.abs(wind_mean_time):0.1f} m/s (No Wind)'),
        *(f'{sun_string}' for _ in (1,) if sun_info), # Only print if sunshine_fraction present
        *(f'{weather.precipitation_mm:.1f} mm' for _ in (1,) if precipitation_info), # Only print if precipitation_mm present
        f'',
        f'',
        f'',
        f'',
        f'+{np.abs(yaw_angle_mean_time):0.1f}° (from behind)' if np.abs(yaw_angle_mean_time)==180 else (f'+{yaw_angle_mean_time:0.1f}° (from right)' if yaw_angle_mean_time > 0 else (f'{yaw_angle_mean_time:0.1f}° (from left)' if yaw_angle_mean_time < 0 else f'{np.abs(yaw_angle_mean_time):0.1f}° (head-on)')),
        f'',
        f'',
        f'',
        f'',
        f'{parameters.weight} kg',
        f'{parameters.power.flat:0.0f}/{parameters.power.uphill:0.0f}/{parameters.power.downhill:0.0f} W',
        f'{parameters.cda.flat}/{parameters.cda.uphill}/{parameters.cda.downhill}',
        f'{datetime.fromtimestamp(parameters.time).strftime("%Y/%m/%d %H:%M:%S")}',
        f'{datetime.now().strftime("%Y/%m/%d %H:%M:%S")}'
    ))

    sources = [*parameters.dem, '© OpenStreetMap contributors', weather_api]
    version_backend, _ = get_version()

    textstr_bottom = '\n'.join((
         f"Data Sources:",
         f"{', '.join(sources)}",
         f'Version {version_backend}'
    ))



    ax4 = plt.subplot(grid[:7, 11:])
    ax4.text(0.05, .52, textstr_left, transform=ax4.transAxes, fontsize=14,
        verticalalignment='center', fontweight='bold')
    ax4.text(0.95, .52, textstr_right, transform=ax4.transAxes, fontsize=14,
        verticalalignment='center', horizontalalignment='right')
    ax4.text(0.05, .05, textstr_bottom, transform=ax4.transAxes, fontsize=10,
        verticalalignment='center', horizontalalignment='left')

    ax4.tick_params(
        which='both',  # both major and minor ticks are affected
        bottom=False,  # ticks along the bottom edge are off
        top=False,  # ticks along the top edge are off
        right=False,  # ticks along the top edge are off
        left=False,  # ticks along the top edge are off
        labelbottom=False,
        labelright=False,
        labelleft=False,
        labeltop=False)

    if parameters.output:
        plt.savefig(meteo_chart_path(parameters.input), dpi=125, facecolor='w', edgecolor='w', orientation='portrait')

def correct_peak_location(parameters, assets):
    print("- Correcting Peak Placement")
    track = assets.track
    lat, lon, timestamp, distance, elevation = (track[column] for column in ('lat', 'lon', 'timestamp', 'distance_m', 'elevation_m'))
    cues = track.cues.to_dict(as_series=False)
    for i, cue_type in enumerate(cues['type']):
        if not (cue_type.lower() == 'summit' or 'category' in cue_type.lower()):
            continue
        # Track point closest to the cue within ±100 m along the track (cue coordinates may be rounded, e.g. by RWGPS)
        trackptidx = np.flatnonzero(np.abs(cues['distance_m'][i] - distance) <= 100)
        if len(trackptidx) == 0:  # Cue not on the track
            continue
        trackptidx = trackptidx[np.argmin((lat[trackptidx] - cues['lat'][i])**2 + ((lon[trackptidx] - cues['lon'][i]) * np.cos(np.deg2rad(cues['lat'][i])))**2)]
        try:
            startidx = (np.abs(distance[trackptidx] - 500 - distance)).argmin()
            endidx = (np.abs(distance[trackptidx] + 500 - distance)).argmin()
            peakidx = 0
            offset = 0
            while peakidx==0 or peakidx==len(range(startidx, endidx)): # In case the whole stretch has no middle peak
                peakidx = (elevation[startidx-offset:endidx+offset]).argmax()
                offset += 1
        except ValueError:  # Empty stretch
            continue
        peakidx = startidx+peakidx
        cues['lat'][i] = lat[peakidx]
        cues['lon'][i] = lon[peakidx]
        cues['timestamp'][i] = timestamp[peakidx]
        if not re.match(r'^\d+_', cues['name'][i]):
            cues['name'][i] = str(str(int(np.round(elevation[peakidx],0)))+'_'+cues['name'][i])[:10]
    track.cues = pl.DataFrame(cues, schema=track.cues.schema)
    return assets

def daylight_warning(parameters, assets) -> str | None:
    """Warning if the ride starts before sunrise or ends after sunset (None if it does not)."""
    import datetime
    from zoneinfo import ZoneInfo
    from timezonefinder import TimezoneFinder
    import astral
    from astral.sun import sun
    try:
        lat, lon, timestamp = assets.track['lat'], assets.track['lon'], assets.track['timestamp']
        start_time = parameters.time
        end_time = start_time + (timestamp[-1] - timestamp[0])
        tf = TimezoneFinder()
        timezone_str_start = tf.timezone_at(lat=lat[0], lng=lon[0])
        l_start = astral.LocationInfo()
        l_start.name = 'name'
        l_start.region = 'region'
        l_start.latitude = lat[0]
        l_start.longitude = lon[0]
        dt_start = datetime.datetime.fromtimestamp(start_time).replace(tzinfo=ZoneInfo(timezone_str_start))
        sunrise = astral.sun.sunrise(l_start.observer, date=dt_start, tzinfo=l_start.timezone)
        sunrise = sunrise.replace(microsecond=0)

        timezone_str_end = tf.timezone_at(lat=lat[-1], lng=lon[-1])
        l_end = astral.LocationInfo()
        l_end.name = 'name'
        l_end.region = 'region'
        l_end.latitude = lat[-1]
        l_end.longitude = lon[-1]
        dt_end = datetime.datetime.fromtimestamp(end_time).replace(tzinfo=ZoneInfo(timezone_str_end))
        sunset = astral.sun.sunset(l_end.observer, date=dt_end, tzinfo=l_end.timezone)
        sunset = sunset.replace(microsecond=0)

        dt_start = dt_start.replace(microsecond=0)
        dt_end = dt_end.replace(microsecond=0)
        if dt_start < sunrise and sunset < dt_end:
            return "Bring lights, the ride is planned to start {} before sunrise and end {} after sunset.".format(
                sunrise - dt_start, dt_end - sunset)
        elif dt_start < sunrise:
            return "Bring lights, the ride is planned to start {} before sunrise.".format(sunrise - dt_start)
        elif sunset < dt_end:
            return "Bring lights, the ride might end {} after sunset.".format(dt_end - sunset)
    except:
        return 'Daylight availability could not be checked.'
    return None

def sunCalc(parameters, assets):
    warning = daylight_warning(parameters, assets)
    if warning:
        print(warning)

def format_time_change(seconds: float) -> str:
    """Signed duration, e.g. '+1:46 min' or '-27 s'."""
    sign = '+' if seconds >= 0 else '-'
    seconds = round(abs(seconds))
    if seconds < 60:
        return f'{sign}{seconds} s'
    return f'{sign}{seconds // 60}:{seconds % 60:02d} min'

def disk_housekeeping(parameters):
    def check_disk_space():
        total, used, free = shutil.disk_usage('/')
        return free / total

    threshold = 0.22
    extensions = ['.tif', '.hgt']  # Specify your file extensions here

    if check_disk_space() < threshold:
        print(f'\t- Only {check_disk_space()*100:0.0f}% disk space left. Housekeeping.')
        dirs = [f'{parameters.folder_prefix}/GeoData/DEM/SRTM3',
                f'{parameters.folder_prefix}/GeoData/DEM/ASTER',
                f'{parameters.folder_prefix}/GeoData/DEM/EUDEM',
                f'{parameters.folder_prefix}/GeoData/DEM/ALOS',
                f'{parameters.folder_prefix}/GeoData/DEM/CopernicusDEM']

        for dir in dirs:
            # Get all files in directory with the specified extensions
            files = [(os.path.join(dir, f), os.path.getatime(os.path.join(dir, f))) for f in os.listdir(dir) if
                     os.path.isfile(os.path.join(dir, f)) and f.endswith(tuple(extensions))]
            # Sort files by last access time
            files.sort(key=lambda x: x[1])

            # While disk space is below threshold, delete oldest files
            while check_disk_space() < threshold:
                if not files:
                    break
                oldest_file = files.pop(0)[0]
                os.remove(oldest_file)
                print(f"\t\t- Deleted {oldest_file}")

    if check_disk_space() < threshold:
        print(f"\t- No more tiles to delete. Still only {check_disk_space()*100:0.0f}% disk space left.")

def climb_detection(assets):
    print("- Detecting Climbs")
    min_length = 500
    min_height_diff = 50
    descent_tolerance = 20
    track = assets.track
    distance = track['distance_m']
    altitude = track['elevation_m']

    climb_start_distance = 200
    climb_start_grade = 0.04

    climbs = []
    climb_start = None
    total_climb = 0
    descent = 0

    # Index of the point closest to 200 m further along the track, for every point
    climb_start_distance_idx = nearest_index(distance, distance + climb_start_distance)

    for i in range(1, len(distance)):
        diff = altitude[i] - altitude[i-1]
        if diff > 0:
            # We're climbing
            j = climb_start_distance_idx[i-1]
            start_grade = (altitude[j]-altitude[i-1])/(distance[j]-distance[i-1])
            if climb_start is None and start_grade>=climb_start_grade:
                climb_start = i-1
            elif climb_start is None and start_grade<climb_start_grade:
                continue
            total_climb += diff
            # Reset the descent counter
            descent = 0
            last_climb_idx = i
        else:
            # We're descending
            descent += abs(diff)
            if descent > np.max([descent_tolerance, total_climb*.05]): # Allowing for bigger descents for long climbs
                # The descent is too big, end the current climb
                if climb_start is not None and total_climb >= min_height_diff and (distance[i-1] - distance[climb_start]) >= min_length:
                    climbs.append((climb_start, last_climb_idx-1))
                climb_start = None
                total_climb = 0

    # Check if there's a climb at the end
    if climb_start is not None and total_climb >= min_height_diff and (distance[-1] - distance[climb_start]) >= min_length:
        climbs.append((climb_start, len(distance)-1))

    for start, end in climbs:
        # Climb Score Idea (COTACOL formula: np.sum(gradient[start:end]**2 * distance[start:end]/1000))
        # HC: >1000, 1: 650 - 1000, 2: 400 - 650, 3: 150 - 400, 4: 50 - 150
        track.add_cue(start, name='ClimbStart', type='Valley',  # Valley
                      notes=f'Climb: {(distance[end] / 1000)-(distance[start] / 1000):0.3f} km @ {((altitude[end]-altitude[start])/(distance[end]-distance[start]))*100:0.1f}%')
        track.add_cue(end, name='KOM', type='Summit', notes=f'{altitude[end]:0.0f}_KOM')  # KOM

def climb_summary(track) -> list[dict]:
    """
    The climbs found by climb_detection(): start_km, top_km, top_m (highest point of the climb),
    length_km and grade_pct (average gradient), in route order.
    """
    distance_km, elevation = track['distance_m'] / 1000, track['elevation_m']
    climbs, start = [], None
    for cue in track.cues.iter_rows(named=True):
        if cue['name'] == 'ClimbStart' and cue['notes'].startswith('Climb: '):
            start = cue
        elif start is not None and cue['type'] == 'Summit' and cue['notes'].endswith('_KOM'):
            match = re.match(r'Climb: ([\d.]+) km @ (-?[\d.]+)%', start['notes'])
            begin_km = start['distance_m'] / 1000
            within = (distance_km >= begin_km) & (distance_km <= cue['distance_m'] / 1000 + 0.5)
            top = np.flatnonzero(within)[np.argmax(elevation[within])] if within.any() else int(np.argmin(np.abs(distance_km - cue['distance_m'] / 1000)))
            climbs.append(dict(start_km=float(begin_km), top_km=float(distance_km[top]), top_m=float(elevation[top]),
                               length_km=float(match.group(1)) if match else float(cue['distance_m'] / 1000 - begin_km),
                               grade_pct=float(match.group(2)) if match else float('nan')))
            start = None
    return climbs

def nearest_index(sorted_values, targets):
    """
    For every target, the index of the closest value in sorted_values (ascending); the first one on ties.
    Same result as np.argmin(np.abs(sorted_values - target)) per target, but O(n log n).
    """
    sorted_values = np.asarray(sorted_values)
    targets = np.asarray(targets)
    if np.any(np.diff(sorted_values) < 0):  # Not sorted: brute force
        return np.array([np.argmin(np.abs(sorted_values - target)) for target in targets])
    right = np.clip(np.searchsorted(sorted_values, targets, side='left'), 0, len(sorted_values) - 1)
    left = np.searchsorted(sorted_values, sorted_values[np.maximum(right - 1, 0)], side='left')  # First occurrence of the value below
    take_left = np.abs(sorted_values[left] - targets) <= np.abs(sorted_values[right] - targets)
    return np.where(take_left, left, right)

def lowess_smoothing(distance, elevation):
    import statsmodels.api as sm
    smoothed = np.full(len(elevation), np.nan)
    known = ~np.isnan(elevation)
    if not known.any():
        return smoothed
    delta = 0.01 * known.sum()  # Distance within which to use linear-interpolation instead of weighted regression.
    frac = 15 / known.sum()  # Between 0 and 1. The fraction of the data used when estimating each y-value.
    smoothed[known] = sm.nonparametric.lowess(elevation[known], distance[known], frac=frac, delta=delta)[:, 1]
    return smoothed

def sizeof_fmt(num, suffix="B"):
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f} {unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


GDAL_CACHE_MB = 64

def getpixelvalues(lat, lon, tilename):
    if os.path.exists(tilename):
        # GDAL caches raster blocks up to 5% of the RAM by default - limit it to keep the memory footprint small
        with rasterio.Env(GDAL_CACHEMAX=GDAL_CACHE_MB), rasterio.open(tilename) as src:
            pixel_values = np.fromiter((x[0] for x in src.sample(zip(lon, lat))), dtype=float, count=len(lat))
    else:
        pixel_values = np.zeros(len(lat))
        if tilename.find('swisstopo') != -1: # Since SA3D values are 10x
            pixel_values[:] = -9990
        else:
            pixel_values[:] = -999
    return pixel_values

def read_shapes(shapefile_path) -> list:
    """All geometries of a shapefile, multi-part geometries split into single parts."""
    import fiona
    import shapely
    from shapely.geometry import shape
    with fiona.open(str(shapefile_path)) as source:
        geometries = [shape(feature['geometry']) for feature in source if feature['geometry'] is not None]
    return list(shapely.get_parts(geometries))

def _points_within(lat, lon, shapes):
    """(point index, shape index) pairs of all points lying within a shape."""
    import shapely
    if len(shapes) == 0 or len(lat) == 0:
        return np.empty((2, 0), dtype=int)
    return shapely.STRtree(shapes).query(shapely.points(lon, lat), predicate='within')

def points_in_shapes(lat, lon, shapes) -> np.ndarray:
    """Boolean mask of the points lying within any of the shapes."""
    mask = np.zeros(len(lat), dtype=bool)
    mask[_points_within(lat, lon, shapes)[0]] = True
    return mask

def count_points_in_shapes(lat, lon, shapes) -> int:
    """Number of (point, shape) pairs with the point lying within the shape."""
    return _points_within(lat, lon, shapes).shape[1]

def get_timezone_offset(assets, parameters):
    from timezonefinder import TimezoneFinder
    from zoneinfo import ZoneInfo
    timezone = TimezoneFinder().timezone_at(lat=assets.track['lat'][0], lng=assets.track['lon'][0])
    offset = datetime.fromtimestamp(parameters.time).replace(tzinfo=ZoneInfo(timezone)).utcoffset()
    return offset.total_seconds()
