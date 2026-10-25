"""
AerOracle web front end (Dash).

Development:  python app.py                      (http://127.0.0.1:8050)
Production:   gunicorn app:server --workers 1 --threads 4 --timeout 600 --bind 127.0.0.1:8050
"""
import base64
import json
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import dash
import dash_bootstrap_components as dbc
import numpy as np
import requests
from dash import Input, Output, State, dcc, html

import assets
import config
import parameters
import plot_interactive
import speedcalculation
import tcx_writer
import utilities
import weather

APP_DIR = Path(__file__).parent
WORK_DIR = Path(tempfile.gettempdir()) / 'aeroracle'  # One sub folder per analysis, holding the processed TCX/GPX/FIT
WORK_RETENTION_S = 30 * 60   # Processed files are deleted after 30 min (legal notice: within 60 min)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ANALYSIS_SLOTS = threading.BoundedSemaphore(2)  # Concurrent analyses (2 cores, 2 GB RAM)
RWGPS_URL = re.compile(r'ridewithgps\.com/(routes|trips)/(\d+)')
PRR_PER_CRR = 8 * 416.925  # Rolling resistance power [W] at 8 m/s per unit Crr (bicyclerollingresistance.com)

# Example route ("Try a sample route"), bundled with its stored weather: no RideWithGPS or weather API calls
EXAMPLE_ROUTE = APP_DIR / 'examples' / 'tdf2022_stage12.json'
EXAMPLE_WEATHER = APP_DIR / 'examples' / 'tdf2022_stage12_weather.json'
EXAMPLE_START = datetime(2022, 7, 14, 12, 15)

DEFAULTS = dict(power=(205, 225, 0), cda=(0.35, 0.40, 0.29), weight=80.0, crr=0.00321)
# Bike presets, not shown yet (the chips are hidden). Gravel values are placeholders to be confirmed.
RIDER_PRESETS = {
    'Road': dict(cda=(0.35, 0.40, 0.29), crr=0.00321),
    'Triathlon/TT': dict(cda=(0.23, 0.23, 0.23), crr=0.00321),
    'Gravel': dict(cda=(0.40, 0.42, 0.32), crr=0.0055),
}

ABOUT = """
AerOracle combines elevation data, weather forecasts, and aerodynamic calculations to estimate the riding time and
weather conditions along a cycling route.

### How to use

1. **Load your route:** upload a TCX, GPX or FIT file, or enter a RideWithGPS link
2. **Set the rider parameters:** power (flat, uphill, downhill), weight, drag area (CdA) and rolling resistance (Crr or Prr)
3. **Choose the options:** automatic climb detection and the weather forecast for a start date and time
4. **Click "Analyze route"**

### Scientific Basis
Martin, J. C., Milliken, D. L., Cobb, J. E., McFadden, K. L., & Coggan, A. R. (1998). Validation of a Mathematical Model for Road Cycling Power. Journal of Applied Biomechanics, 14(3), 276-291. [https://doi.org/10.1123/jab.14.3.276](https://doi.org/10.1123/jab.14.3.276)
"""

DATA_SOURCES = """
- **Weather**
    - [Weather data by Open-Meteo.com](https://open-meteo.com/), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
    - [Weather data provided by Visual Crossing](https://www.visualcrossing.com/)
    - [meteoblue](https://www.meteoblue.com/)
- **Elevation**
    - [swissALTI3D](https://www.swisstopo.admin.ch/en/height-model-swissalti3d) and swissBOUNDARIES3D: source: Federal Office of Topography swisstopo
    - [LiDAR DTMs by Sonny](https://sonny.4lima.de/), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
    - [Copernicus DEM](https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM):
      produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018
      provided under COPERNICUS by the European Union and ESA; all rights reserved
    - [ALOS World 3D - 30 m (AW3D30)](https://www.eorc.jaxa.jp/ALOS/en/dataset/aw3d30/aw3d30_e.htm) © JAXA
    - [EU-DEM](https://land.copernicus.eu/imagery-in-situ/eu-dem): produced using Copernicus data and information funded by the European Union - EU-DEM layers
    - [ASTER GDEM V003](https://doi.org/10.5067/ASTER/ASTGTM.003): NASA/METI/AIST/Japan Spacesystems and U.S./Japan ASTER Science Team
    - [SRTM](https://www.earthdata.nasa.gov/data/instruments/srtm): NASA Shuttle Radar Topography Mission
- **Gravity**
    - [World Gravity Map WGM2012](https://doi.org/10.18168/bgi.23): Bonvalot, S. et al. (2012), BGI, CGMW, CNES, IRD
- **Maps, tunnels and bridges**
    - © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), data under the Open Database License (ODbL)
- **Example route**
    - Tour de France 2022, stage 12, from [RideWithGPS](https://ridewithgps.com/), weather of 14 July 2022 from the Open-Meteo archive
"""


def svg_data_uri(svg):
    return 'data:image/svg+xml;utf8,' + quote(svg)


LOGO = svg_data_uri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 34 34"><g fill="none" stroke="#fff" stroke-linecap="round">'
    '<circle cx="17" cy="17" r="14" stroke-width="2.5"/><path d="M3 13h9M1 18h11M3 23h9" stroke-width="2.2" opacity=".7"/></g>'
    '<circle cx="17" cy="17" r="2.5" fill="#fff"/></svg>')


def example_profile_svg():
    """Gradient-colored elevation profile of the example route for the start page."""
    try:
        points = json.loads(EXAMPLE_ROUTE.read_text())['route']['track_points']
    except (OSError, KeyError, ValueError):
        return None
    km = np.array([p['d'] for p in points]) / 1000
    elevation = np.convolve([p['e'] for p in points], np.ones(15) / 15, mode='same')
    floor, top = elevation.min() - 150, elevation.max() + 100
    x = lambda k: 10 + k / km[-1] * 500
    y = lambda e: 240 - (e - floor) / (top - floor) * 220
    edges = np.linspace(0, len(km) - 1, 300).round().astype(int)
    shapes = []
    for a, b in zip(edges[:-1], edges[1:]):
        gradient = (elevation[b] - elevation[a]) / max((km[b] - km[a]) * 1000, 1) * 100
        color = plot_interactive.gradient_color(gradient)
        shapes.append(f'<path d="M{x(km[a]):.1f},240 L{x(km[a]):.1f},{y(elevation[a]):.1f} L{x(km[b]):.1f},{y(elevation[b]):.1f} '
                      f'L{x(km[b]):.1f},240 Z" fill="{color}" stroke="{color}" stroke-width=".5"/>')
    line = ' '.join(f'{x(km[i]):.1f},{y(elevation[i]):.1f}' for i in edges)
    return svg_data_uri(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 520 250">{"".join(shapes)}'
                        f'<polyline points="{line}" fill="none" stroke="#4a5866" stroke-width="1.5"/></svg>')


EXAMPLE_PROFILE = example_profile_svg()


# --- Housekeeping of the processed files ---

def cleanup_work_dir():
    """Delete analysis folders older than WORK_RETENTION_S."""
    if not WORK_DIR.exists():
        return
    for folder in WORK_DIR.iterdir():
        try:
            if time.time() - folder.stat().st_mtime > WORK_RETENTION_S:
                shutil.rmtree(folder, ignore_errors=True)
        except FileNotFoundError:
            pass


def cleanup_loop():
    while True:
        cleanup_work_dir()
        time.sleep(300)


threading.Thread(target=cleanup_loop, daemon=True, name='aeroracle-cleanup').start()


# --- Progress of running analyses ---

class ProgressLog:
    """
    Steps of the running analyses, taken from the model's console output ("- Replacing Elevation Data", ...).
    Output is attributed to an analysis by the thread running it, so concurrent analyses do not mix.
    """
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()
        self._local = threading.local()

    def start(self, job_id):
        with self._lock:
            self._jobs[job_id] = dict(started=time.time(), steps=[])
        self._local.job = job_id

    def finish(self):
        job_id = getattr(self._local, 'job', None)
        self._local.job = None
        with self._lock:
            self._jobs.pop(job_id, None)

    def add(self, text):
        """Add the step(s) printed by the current thread's analysis; level 1 for indented (tab) lines."""
        job_id = getattr(self._local, 'job', None)
        if job_id is None:
            return
        for line in text.splitlines():
            message = line.strip().lstrip('-').strip()
            if message and not message.startswith('└'):  # Resource monitoring details
                with self._lock:
                    if job_id in self._jobs:
                        self._jobs[job_id]['steps'].append((1 if line.startswith('\t') else 0, message))

    def status(self, job_id):
        """(seconds since the start, [(level, message), ...]) or None if the analysis is not running."""
        with self._lock:
            job = self._jobs.get(job_id)
            return (time.time() - job['started'], list(job['steps'])) if job else None


class ProgressStdout:
    """Console output that also feeds the progress log."""
    def __init__(self, stream, progress):
        self._stream, self._progress = stream, progress

    def write(self, text):
        self._progress.add(text)
        return self._stream.write(text)

    def __getattr__(self, name):
        return getattr(self._stream, name)


PROGRESS = ProgressLog()
sys.stdout = ProgressStdout(sys.stdout, PROGRESS)


def progress_panel(elapsed=0, steps=()):
    steps = list(steps) or [(0, "Starting the analysis")]
    return html.Div([
        html.Div([html.Span(className='ao-spinner'), html.B("Analyzing your route"), html.Span(f"{elapsed:.0f} s", className='ao-hint')],
                 className='ao-progress-head'),
        html.Ol([html.Li(message, className=('current' if i == len(steps) - 1 else 'done') + (' sub' if level else ''))
                 for i, (level, message) in enumerate(steps)]),
    ], className='ao-card ao-card-body')


# --- Analysis ---

class AnalysisError(Exception):
    """Error with a message for the user."""


def save_route(key, upload_contents, upload_filename, url, example=False):
    """
    Write the route to the analysis folder: the uploaded file, the downloaded RideWithGPS JSON or a copy of the example.
    Returns (path, file name stem for the downloads).
    """
    folder = WORK_DIR / key
    folder.mkdir(parents=True)
    if example:
        path = folder / 'input.json'
        shutil.copy(EXAMPLE_ROUTE, path)
        return path, EXAMPLE_ROUTE.stem

    if upload_contents:
        extension = Path(upload_filename).suffix.lower()
        if extension not in ('.tcx', '.gpx', '.fit'):
            raise AnalysisError('Please upload a TCX, GPX or FIT file.')
        data = base64.b64decode(upload_contents.split(',', 1)[1])
        if len(data) > MAX_UPLOAD_BYTES:
            raise AnalysisError('Maximum file size: 10 MB')
        path = folder / f'input{extension}'
        path.write_bytes(data)
        return path, Path(upload_filename).stem

    match = RWGPS_URL.search(url or '')
    if not match:
        raise AnalysisError('Could not extract route/trip ID from the link. Please use the format https://ridewithgps.com/routes/12345678 '
                            '(optionally with ?privacy_code=XYZ for private routes/trips)')
    route_type, route_id = match.groups()
    path = folder / 'input.json'
    try:
        utilities.rwgps_download(SimpleNamespace(url=url, input=str(path)))
    except SystemExit:
        raise AnalysisError(f'Failed to download {route_type[:-1]} from RideWithGPS. Please check the link and the privacy code '
                            f'in the link in case of a private {route_type[:-1]}.')
    except requests.exceptions.RequestException as e:
        raise AnalysisError(f'Network error while downloading from RideWithGPS: {e}')
    return path, f'ridewithgps_{route_type}_{route_id}'


def analyze(route_path, settings):
    """Run the model; returns (args, data)."""
    argv = [
        '--input', str(route_path),
        '--power', str(settings['power_flat']), str(settings['power_up']), str(settings['power_down']),
        '--weight', str(settings['weight']),
        '--cda', str(settings['cda_flat']), str(settings['cda_up']), str(settings['cda_down']),
        '--crr', str(settings['crr']),
        '--quiet',
    ]
    if settings['climbs']:
        argv.append('--climbs')
    args = parameters.Parameters(argv)
    args.time = settings['start_time'] or False  # Unix timestamp, bypassing the date parsing of the command line
    args.output = False
    args.weather_file = settings.get('weather_file')  # Stored weather instead of API calls (example route)

    data = assets.Assets(args)
    data = speedcalculation.__init__(args, data)
    if args.climbs:
        utilities.climb_detection(data)
    data = utilities.correct_peak_location(args, data)
    return args, data


def write_outputs(key, data, course_name):
    """Write route.tcx (and route.gpx, route.fit if gpsbabel is available) to the analysis folder."""
    folder = WORK_DIR / key
    tcx_path = folder / 'route.tcx'
    tcx_writer.write_tcx(str(tcx_path), data.track)
    if course_name:
        tcx_writer.update_tcx_name(tcx_path, course_name)
    for suffix in tcx_writer.GPSBABEL_FORMATS:
        tcx_writer.export(tcx_path, folder / f'route{suffix}', data.track)


def log_analysis(data, success):
    try:
        import tracker
        if success:
            height_diff = np.diff(data.track['elevation_m'])
            tracker.log_analysis(distance_km=data.track['distance_m'][-1] / 1000,
                                 elevation_gain_m=int(np.sum(height_diff[height_diff > 0])), success=True)
        else:
            tracker.log_analysis(distance_km=None, elevation_gain_m=None, success=False)
    except Exception:
        pass  # Don't fail the app if tracking fails


def summit_markers(track, climbs):
    """The summits of all detected climbs (as in the climbs table), or the highest point without climbs; with their track point index."""
    distance_km = track['distance_m'] / 1000
    summits = climbs
    if not summits:
        top = int(np.nanargmax(track['elevation_m']))
        summits = [dict(top_km=float(distance_km[top]), top_m=float(track['elevation_m'][top]))]
    return [dict(s, index=int(np.argmin(np.abs(distance_km - s['top_km'])))) for s in summits]


# --- Result layout ---

def format_duration(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def gradient_scale_legend():
    """Gradient bands as one bar, labeled at the boundaries like a ruler."""
    colors, boundaries = plot_interactive.gradient_scale()
    return html.Div([
        html.Span("Gradient", className='ao-gradscale-unit ao-gradscale-name'),
        html.Div([
            html.Div([html.I(style={'background': color}) for color in colors], className='ao-gradscale-bar'),
            html.Div([html.Span(f"{limit:g}".replace('-', '−'), style={'left': f'{100 * (k + 1) / len(colors):.3f}%'})
                      for k, limit in enumerate(boundaries)], className='ao-gradscale-ticks'),
        ], className='ao-gradscale-ruler'),
        html.Span("%", className='ao-gradscale-unit'),
    ], className='ao-gradscale', title="Gradient [%] of the profile's color bands")


def card(title, children, hint=None, head_extra=None, body_class='ao-card-body'):
    head = [html.H2(title)]
    if hint or head_extra:
        head.append(html.Div([html.Span(hint, className='ao-hint') if hint else None, head_extra],
                             style={'display': 'flex', 'gap': '12px', 'alignItems': 'center', 'flexWrap': 'wrap'}))
    return html.Section([html.Div(head, className='ao-card-head'), html.Div(children, className=body_class)], className='ao-card')


def computer_field(label, value, unit, sub=None):
    return html.Div([html.Span(label, className='ao-label'), html.Span([value, html.Small(unit)], className='ao-big'),
                     *([html.Span(sub, className='ao-sub')] if sub else [])], className='ao-cfield')


WHATIF_LABEL_SPACE = '72px'  # Kept free on both sides of the bars for their labels


def whatif_chart(sensitivities, pct):
    """Horizontal bars per parameter: −pct (pale) and +pct (solid), green = faster, red = slower."""
    rows = sorted(sensitivities, key=lambda s: -abs(s.plus_s))
    limit_min = max(1, int(np.ceil(max(max(abs(s.minus_s), abs(s.plus_s)) for s in rows) * 1.3 / 60)))
    tick_step = 1 if limit_min <= 3 else 2
    limit_min = int(np.ceil(limit_min / tick_step) * tick_step)
    limit = limit_min * 60
    # Position on the track: the center line ± half of the chart width minus the label space
    position = lambda fraction: f'calc(50% + (50% - {WHATIF_LABEL_SPACE}) * {fraction:.4f})'

    def bar(seconds, kind):
        fraction = abs(seconds) / limit
        faster = seconds < 0  # Faster grows to the left of the center line, its label beyond the bar's outer end
        side = 'right' if faster else 'left'
        return [html.Div(className=f"ao-bar {kind} {'faster' if faster else 'slower'}",
                         style={side: '50%', 'width': f'calc((50% - {WHATIF_LABEL_SPACE}) * {fraction:.4f})'}),
                html.Div(utilities.format_time_change(seconds), className='ao-bar-label',
                         style={side: f'calc({position(fraction)} + 6px)', 'top': '2px' if kind == 'minus' else '22px'})]

    ticks = [html.Span(f"{'−' if m < 0 else '+' if m > 0 else ''}{abs(m)} min" if m else '0', style={'left': position(m / limit_min)})
             for m in range(-limit_min, limit_min + 1, tick_step)]
    return html.Div([
        html.Div([html.Div(), html.Div([html.Span("← faster", className='faster'), html.Span("slower →", className='slower')],
                                       className='ao-whatif-direction')], className='ao-whatif-axis'),
        *[html.Div([html.Div([html.B(s.parameter), html.Span(f"{pct:g}% = {s.step}")], className='ao-whatif-name'),
                    html.Div(bar(s.minus_s, 'minus') + bar(s.plus_s, 'plus'), className='ao-whatif-bars')], className='ao-whatif-row')
          for s in rows],
        html.Div([html.Div(), html.Div(ticks, className='ao-whatif-ticks')], className='ao-whatif-axis'),
        html.Div([html.Span([html.I(style={'opacity': .5}), f"−{pct:g}%"]), html.Span([html.I(), f"+{pct:g}%"]),
                  html.Span("Only this parameter changes; power and CdA for flat, uphill and downhill together")], className='ao-whatif-key'),
    ], className='ao-whatif')


def climbs_table(args, climbs):
    if not args.climbs:
        return html.P("Climb detection is off (step 3).", className='ao-hint')
    if not climbs:
        return html.P("No climbs of at least 500 m length and 50 m height found.", className='ao-hint')
    return html.Div(html.Table([
        html.Thead(html.Tr([html.Th("Top"), html.Th("Start at km"), html.Th("Top at km"), html.Th("Length"), html.Th("Average")])),
        html.Tbody([html.Tr([
            html.Td(f"{c['top_m']:.0f} m"), html.Td(f"{c['start_km']:.1f}"), html.Td(f"{c['top_km']:.1f}"), html.Td(f"{c['length_km']:.1f} km"),
            html.Td([f"{c['grade_pct']:.1f}%", html.Span(className='ao-gbar', style={
                'width': f"{max(4, round(c['grade_pct'] * 5))}px", 'background': plot_interactive.gradient_color(c['grade_pct'])})]),
        ]) for c in climbs]),
    ], className='ao-climbs'), className='ao-tablewrap')


def uv_info(uv_index):
    """Protection advice for a UV index (Swiss Federal Office of Public Health, BAG)."""
    if uv_index < 3:
        return "Low: no protection required."
    elif uv_index < 6:
        return "Moderate: stay in shade near midday. Protection required: wear shirt, sunglasses and sunscreen."
    elif uv_index < 8:
        return "High: reduce sun exposure between 10:00 and 16:00. Protection required: wear shirt, sunglasses and sunscreen."
    elif uv_index < 11:
        return "Very high: minimize sun exposure between 10:00 and 16:00. Additional protection required: seek shade and wear shirt, sunglasses and sunscreen."
    return "Extreme: avoid sun exposure between 10:00 and 16:00. Additional protection required: seek shade and wear shirt, sunglasses and sunscreen."


def conditions(args, data):
    track, weather = data.track, data.weather_summary
    wind = utilities.time_weighted_mean(track, track['headwind_ms'])
    yaw = np.round(utilities.time_weighted_mean(track, track['yaw_angle_deg']) * 10) / 10
    warning = utilities.daylight_warning(args, data)
    items = [
        ("Inline wind", f"{abs(wind):.1f} m/s", f"{'head' if wind >= 0 else 'tail'} wind, time-weighted"),
        ("Mean yaw", f"{yaw:+.1f}°", "wind from the right" if yaw > 0 else "wind from the left" if yaw < 0 else "head-on"),
    ]
    if weather.sunshine_fraction is not None:
        items.append(("Sunshine", f"{weather.sunshine_fraction * 100:.0f}%", "of riding time"))
    if weather.precipitation_mm is not None:
        items.append(("Precipitation", f"{weather.precipitation_mm:.1f} mm", "during the ride"))
    if warning:  # Only when part of the ride is in the dark
        items.append(("Daylight", "Bring lights", warning))
    uv = weather.uv_index_max
    help_texts = {} if uv is None else {
        "UV index": f"UV index {uv:.1f}. {uv_info(uv)} Source: Swiss Federal Office of Public Health (BAG), www.bag.admin.ch/uv-index"}
    items.append(("UV index", f"{uv:.1f}" if uv is not None else "n/a", uv_info(uv).split(':')[0] if uv is not None
                  else "only in forecasts, not for past dates"))
    return html.Div([html.Div([html.Span(label, className='ao-label' + (' ao-has-help' if label in help_texts else '')), html.B(value), html.Small(sub)],
                              title=help_texts.get(label)) for label, value, sub in items], className='ao-wx')


def dem_sources(track):
    """Names of the DEMs the elevations come from, the one used for most points first."""
    return [str(name) for name, _ in track.points['dem_source'].drop_nulls().value_counts(sort=True).iter_rows()]


# FIT course point types (tcx_writer.FIT_COURSE_POINT_TYPES) of climbs and of turn-by-turn directions
CLIMB_CUE_TYPES = {'summit', 'valley', 'fourth_category', 'third_category', 'second_category', 'first_category', 'hors_category'}
TURN_CUE_TYPES = {'left', 'right', 'straight', 'left_fork', 'right_fork', 'middle_fork', 'slight_left', 'sharp_left',
                  'slight_right', 'sharp_right', 'u_turn'}


def climbs_detected(count):
    return "No climbs detected" if count == 0 else f"{count} climb{'' if count == 1 else 's'} detected"


def cue_contents(track):
    """What the cues of the downloadable files contain, e.g. "Contains climbs and turn-by-turn directions as cues" (None without either)."""
    types = [tcx_writer.fit_course_point_type(t) for t in track.cues['type'].to_list()]
    count = lambda names: sum(t in {tcx_writer.FIT_COURSE_POINT_TYPES[n] for n in names} for t in types)
    summits = count(CLIMB_CUE_TYPES - {'valley'})  # One summit (or category) cue per climb
    contents = []
    if count(CLIMB_CUE_TYPES):
        contents.append("a climb" if summits == 1 else "climbs")
    if count(TURN_CUE_TYPES):
        contents.append("turn-by-turn directions")
    return f"Contains {' and '.join(contents)} as cues" if contents else None


def results_layout(key, args, data, settings, theme='light'):
    track = data.track
    timestamp, distance_km, elevation = track['timestamp'], track['distance_m'] / 1000, track['elevation_m']
    ride_s = timestamp[-1] - timestamp[0]
    height_diff = np.diff(elevation)
    ascent, descent = float(np.sum(height_diff[height_diff > 0])), float(-np.sum(height_diff[height_diff < 0]))
    has_weather = args.time is not False and track.has('temperature_c')
    climbs = utilities.climb_summary(track) if args.climbs else []
    summits = summit_markers(track, climbs)
    finish = datetime.fromtimestamp(args.time + ride_s).strftime('%H:%M') if args.time else None

    meta = []
    if args.time:
        meta += [("Start", datetime.fromtimestamp(args.time).strftime('%a %-d %b %Y, %H:%M')),
                 ("Weather provider", f"{utilities.WEATHER_PROVIDER_NAMES[args.weather]}{' (stored)' if settings.get('weather_file') else ''}")]
    dems = dem_sources(track)
    meta += [("DEMs" if len(dems) > 1 else "DEM", ', '.join(dems) or "none"),
             ("Power", f"{args.power.flat:.0f} / {args.power.uphill:.0f} / {args.power.downhill:.0f} W")]

    fields = html.Section([
        computer_field("Riding time", format_duration(ride_s), "h", f"Finishing time {finish}" if finish else None),
        computer_field("Total distance", f"{distance_km[-1]:.2f}", "km", climbs_detected(len(climbs)) if args.climbs else None),
        computer_field("Average speed", f"{distance_km[-1] / ride_s * 3600:.2f}", "km/h"),
        computer_field("Ascent", f"+{ascent:.0f}", "m"),  # Highest point and finish elevation: see the profile
        computer_field("Descent", f"−{descent:.0f}", "m"),
        computer_field("Normalized power", f"{args.power_out.normalized_w:.0f}", "W", f"average {args.power_out.average_w:.0f} W"),
    ], className='ao-fieldsbar', **{'aria-label': 'Key figures'})

    pills = [html.Span([html.B(f"{args.power_out.average_w * ride_s / 1000:.0f}"), " kcal"], className='ao-pill')]
    if getattr(data, 'tunnel_distance', 0):
        pills.append(html.Span([html.B(f"{data.tunnel_distance:.2f} km"), " tunnels & underpasses"], className='ao-pill'))
    # Weather is shown in the profile (temperature) and the conditions card only

    options = plot_interactive.color_options(args, data)
    selected = 'Wind' if 'Wind' in options else 'Speed'
    sources = ', '.join(['© OpenStreetMap contributors', *([utilities.WEATHER_PROVIDER_NAMES[args.weather]] if has_weather else []), *args.dem])
    route_card = html.Section([
        html.Div([html.H2("Route"), html.Div([html.Label("Color by", htmlFor='map-color', className='ao-hint'),
                                              dbc.Select(id='map-color', options=list(options), value=selected, size='sm')],
                                             className='ao-colorby')], className='ao-card-head'),
        dcc.Graph(id='route-map', figure=plot_interactive.map_figure(args, data, options, selected, summits, theme=theme),
                  config={'scrollZoom': True, 'displaylogo': False}),
        html.Div([
            html.Div([html.Span("Elevation and temperature profile" if has_weather else "Elevation profile", className='ao-label'),
                      gradient_scale_legend()],
                     style={'display': 'flex', 'justifyContent': 'space-between', 'gap': '12px', 'flexWrap': 'wrap', 'alignItems': 'center'}),
            dcc.Graph(id='route-profile', figure=plot_interactive.profile_figure(args, data, summits, theme=theme), config={'displaylogo': False}),
            html.Div(f"Hover the profile or the route: both show the same point. Data: {sources}", className='ao-hint'),
        ], className='ao-card-body', style={'borderTop': '1px solid var(--ao-line)'}),
        dcc.Store(id='map-color-options', data=options),
    ], className='ao-card')

    folder = WORK_DIR / key
    def download_button(fmt):
        path = folder / f'route.{fmt.lower()}'
        size = path.stat().st_size if path.exists() else 0
        size_text = f"{size / 1e6:.1f} MB" if size > 1e6 else f"{size / 1e3:.0f} kB"
        return html.Button([html.B(fmt), html.Span(size_text if size else "needs gpsbabel 1.10 or newer")],
                           id=f'download-{fmt.lower()}-button', className='ao-dl', disabled=not size, n_clicks=0)

    return html.Div([
        html.Div([html.Span([html.Span(f"{label}: ", className='ao-meta-label'), value]) for label, value in meta], className='ao-ride-meta'),
        *([dbc.Alert(utilities.daylight_warning(args, data), color='warning', className='mb-0')]
          if args.time and utilities.daylight_warning(args, data) else []),
        fields,
        html.Div(pills, className='ao-pills'),
        route_card,
        html.Div([card(f"What if {args.whatif_pct:g}% changes?", whatif_chart(speedcalculation.sensitivities(args, track), args.whatif_pct), hint="Only one parameter changes at a time"),
                  card("Climb" if len(climbs) == 1 else "Climbs", climbs_table(args, climbs), hint=f"{len(climbs)} detected" if args.climbs else None)],
                 className='ao-twocol'),
        *([card("Conditions", conditions(args, data),
                hint=f"One {utilities.WEATHER_PROVIDER_NAMES[args.weather]} forecast per {weather.FORECAST_INTERVAL_S // 60} min of riding time"
                     + (" (stored for the example)" if settings.get('weather_file') else ""))]
          if has_weather else []),
        card("Take it to your head unit", [
            html.Div([download_button("TCX"), download_button("FIT"), download_button("GPX")], className='ao-downloads'),
            html.Div(id='download-message'),
        ], hint=cue_contents(track)),
    ], className='ao-results')


def start_page():
    return html.Div([
        html.Section([
            html.Div([
                html.Div("Physics model · open elevation & weather data", className='ao-label'),
                html.H1("How long will your ride really take?"),
                html.P("Load a route, set your power and position, and AerOracle estimates the riding time from elevation, wind, "
                       "temperature and air density along every metre."),
                html.Div([dbc.Button("Try a sample route", id='try-sample', color='primary'),
                          html.A("Load your route", href='#setup', className='btn btn-outline-primary')], className='ao-cta'),
            ], className='ao-hero-text'),
            html.Div(html.Img(src=EXAMPLE_PROFILE, alt="Elevation profile of the sample route") if EXAMPLE_PROFILE else None, className='ao-hero-art'),
        ], className='ao-hero'),
        html.Div([
            html.Div([html.H3("Wind along the route"), html.P("Forecast every 10 minutes of riding time, projected onto your direction of travel.")], className='ao-feature'),
            html.Div([html.H3("Precise elevation"), html.P("swissALTI3D and LiDAR where available, Copernicus worldwide, tunnels and bridges flattened.")], className='ao-feature'),
            html.Div([html.H3("Ready for your head unit"), html.P("TCX, GPX and FIT courses with climbs and turns as cues.")], className='ao-feature'),
        ], className='ao-features'),
    ], className='ao-start')


# --- Page layout ---

ROLLING_RESISTANCE_URL = 'https://www.bicyclerollingresistance.com/'


def rolling_resistance_hint(rr_type):
    if rr_type == "Prr":  # Prr values come from the tire tests of bicyclerollingresistance.com
        link = html.A("bicyclerollingresistance.com", href=ROLLING_RESISTANCE_URL, target='_blank', rel='noopener noreferrer')
        return ["Tire test values from ", link, ": ≤12 W fast, 12–20 W medium, ≥20 W slow"]
    return "Typical: 0.002–0.003 race tubulars, 0.003–0.004 performance clinchers, 0.004–0.005 training"


def field(label, component, label_for=None):
    return html.Div([html.Label(label, htmlFor=label_for), component], className='ao-field')


def number(id, value, step, min=0, max=None):
    return dbc.Input(id=id, type='number', value=value, step=step, min=min, max=max, size='sm')


def segmented(id, options, value):
    return dbc.RadioItems(id=id, options=options, value=value, inline=True, class_name='ao-seg',
                          label_class_name='ao-seg-btn', label_checked_class_name='active')


def step(number_, title, children):
    return html.Section([html.Div([html.Span(number_, className='ao-step-no'), html.H2(title)], className='ao-step-head'), *children],
                        className='ao-step')


def rail():
    today = date.today()
    return html.Aside([
        step("1", "Route", [
            segmented('input-method', ["Upload file", "RideWithGPS link"], "Upload file"),
            html.Div(id='upload-section', children=[
                dcc.Upload(id='upload', accept='.tcx,.gpx,.fit', max_size=MAX_UPLOAD_BYTES, className='ao-dropzone',
                           children=html.Div(["Drop a TCX, GPX or FIT file or ", html.U("select one")])),
                html.Div("Maximum file size: 10 MB", className='ao-hint'),
            ]),
            html.Div(id='url-section', hidden=True, children=[
                dbc.Input(id='url', placeholder="https://ridewithgps.com/routes/12345678?privacy_code=…", size='sm'),
            ]),
            html.Div(id='route-status'),
            html.Button("Or try the sample route", id='try-sample-rail', className='ao-link', n_clicks=0),
            dbc.Checkbox(id='course-rename', label="Rename course for the head unit", value=False),
            dbc.Input(id='course-name', placeholder="Beautiful Ride", size='sm', maxLength=tcx_writer.MAX_COURSE_NAME_LEN, style={'display': 'none'}),
        ]),
        step("2", "Rider", [
            # Bike presets: kept, but hidden for now
            html.Div(segmented('rider-preset', list(RIDER_PRESETS), 'Road'), hidden=True),
            html.Div([html.Div([html.Span("Power", className='ao-label'), html.Span("W", className='ao-hint')], className='ao-unit-row'),
                      html.Div([field("Flat", number('power-flat', DEFAULTS['power'][0], 1, max=3000), 'power-flat'),
                                field("Uphill", number('power-up', DEFAULTS['power'][1], 1, max=3000), 'power-up'),
                                field("Downhill", number('power-down', DEFAULTS['power'][2], 1, max=3000), 'power-down')], className='ao-fields')]),
            html.Div([html.Div([html.Span("CdA", className='ao-label'), html.Span("m²", className='ao-hint')], className='ao-unit-row'),
                      html.Div([field("Flat", number('cda-flat', DEFAULTS['cda'][0], 0.005, max=1), 'cda-flat'),
                                field("Uphill", number('cda-up', DEFAULTS['cda'][1], 0.005, max=1), 'cda-up'),
                                field("Downhill", number('cda-down', DEFAULTS['cda'][2], 0.005, max=1), 'cda-down')], className='ao-fields')]),
            field("System weight, kg (bike, accessories and rider)", number('weight', DEFAULTS['weight'], 0.1, max=200), 'weight'),
            html.Div([
                html.Label("Rolling resistance coefficient (Crr)", id='rr-label', htmlFor='rr-value'),
                html.Div([segmented('rr-type', ["Crr", "Prr"], "Crr"), number('rr-value', DEFAULTS['crr'], 0.0001)], className='ao-rr'),
                html.Span(rolling_resistance_hint("Crr"), id='rr-hint', className='ao-hint'),
            ], className='ao-field'),
        ]),
        step("3", "Options", [
            dbc.Switch(id='climbs', label="Detect climbs", value=True),
            dbc.Switch(id='weather-on', label="Weather forecast", value=False),
            html.Div(id='start-time-section', hidden=True, children=html.Div([
                field("Start date", dcc.DatePickerSingle(
                    id='start-date', date=today, min_date_allowed=date(2000, 1, 1), max_date_allowed=today + timedelta(days=7),
                    display_format='YYYY-MM-DD', first_day_of_week=1)),  # Monday (browsers expose only the language, not the regional format)
                field("Local start time (24 h)", dbc.Input(id='start-time', type='text', placeholder='HH:MM', size='sm'), 'start-time'),  # A native time input follows the browser language
            ], className='ao-fields two')),
            html.Div("Up to 7 days ahead, or a past date", className='ao-hint'),
        ]),
        dbc.Button("Analyze route", id='analyze', color='primary', className='ao-analyze'),
    ], className='ao-rail', id='setup', **{'aria-label': 'Setup'})


def header():
    version_backend, _ = utilities.get_version()
    return html.Header(html.Div([
        html.A([html.Img(src=LOGO, alt=""), html.Span("AerOracle", className='ao-brand-name'),
                html.Span("Ride time, wind & weather for your route", className='ao-brand-tag')], href='/', className='ao-brand'),
        html.Nav([dbc.Button("About", id='open-about', color='link'), dbc.Button("Data sources", id='open-sources', color='link'),
                  dbc.Button("Legal notice", id='open-legal', color='link')], className='ao-topnav'),
        html.Span(f"v{version_backend}", className='ao-version'),
    ], className='ao-topbar-inner'), className='ao-topbar')


def modal(id, title, markdown):
    return dbc.Modal(id=id, size='lg', scrollable=True, children=[
        dbc.ModalHeader(dbc.ModalTitle(title)),
        dbc.ModalBody(dcc.Markdown(markdown, dangerously_allow_html=True, link_target='_blank')),
    ])


def serve_layout():
    version_backend, version_frontend = utilities.get_version()
    osm_data_date = utilities.get_osm_data_date()
    osm_info = f" · OSM data as of {osm_data_date}" if osm_data_date else ""
    return html.Div([
        header(),
        html.Div([
            rail(),
            html.Div([html.Div(id='progress', hidden=True, className='ao-progress'),
                      html.Main(id='results', children=start_page())], className='ao-main'),
        ], className='ao-shell'),
        html.Footer(["AerOracle by SARcycle",
                     *([" · ", html.A("Source code", href=config.SOURCE_URL, target='_blank', rel='noopener')] if config.SOURCE_URL else []),
                     f" · Developed for cycling nerds just like me · Backend {version_backend} · Frontend {version_frontend}{osm_info}"],
                    className='ao-footer'),
        modal('about-modal', "About AerOracle", ABOUT),
        modal('sources-modal', "Data sources", DATA_SOURCES),
        modal('legal-modal', "Legal notice", (APP_DIR / 'legal_notice.md').read_text()),
        dcc.Store(id='analysis-key'),
        dcc.Store(id='sample-request'),
        dcc.Store(id='job'),  # {'id': ..., 'example': ...} of the analysis started last
        dcc.Interval(id='progress-poll', interval=500, disabled=True),
        dcc.Store(id='theme'),  # 'light' or 'dark', from the system setting (theme.js)
        dcc.Interval(id='page-loaded', interval=200, max_intervals=1),  # Fires once after loading: default start time
        dcc.Store(id='hover-sync-dummy'),
        dcc.Store(id='map-color-dummy'),
        dcc.Download(id='download'),
    ])


app = dash.Dash(
    __name__,
    title="AerOracle - Aerodynamic Cycling Modeling Tool",
    assets_folder=str(APP_DIR / 'dash_assets'),  # Not "assets": that is the name of a project module
    meta_tags=[{'name': 'robots', 'content': 'noindex, nofollow'}, {'name': 'googlebot', 'content': 'noindex, nofollow'}],
    suppress_callback_exceptions=True,  # Result components are created by callbacks
    compress=True,  # gzip responses (flask-compress): the analysis result is ~1 MB of JSON
)
app.layout = serve_layout
server = app.server  # For gunicorn


@server.route('/robots.txt')
def robots_txt():
    return (APP_DIR / 'robots.txt').read_text(), 200, {'Content-Type': 'text/plain'}


# --- Callbacks ---

@app.callback(Output('upload-section', 'hidden'), Output('url-section', 'hidden'), Input('input-method', 'value'))
def toggle_input_method(method):
    return method != "Upload file", method == "Upload file"


@app.callback(Output('route-status', 'children'), Input('upload', 'filename'), Input('url', 'value'), Input('input-method', 'value'),
              State('upload', 'contents'))
def show_route_status(filename, url, method, contents):
    if method == "Upload file":
        if not filename or not contents:
            return None
        size_mb = len(contents.split(',', 1)[-1]) * 3 / 4 / 1024 / 1024
        return html.Div([html.B(filename), html.Span(f"{size_mb:.2f} MB, ready to analyze")], className='ao-routechip')
    if not url:
        return None
    match = RWGPS_URL.search(url)
    if "ridewithgps.com" not in url or not match:
        return html.Div("Please enter a RideWithGPS route or trip link, e.g. https://ridewithgps.com/routes/12345678", className='ao-error')
    return html.Div([html.B(f"RideWithGPS {match.group(1)[:-1]} {match.group(2)}"), html.Span("ready to analyze")], className='ao-routechip')


@app.callback(Output('course-name', 'style'), Input('course-rename', 'value'))
def toggle_course_name(rename):
    return {} if rename else {'display': 'none'}


@app.callback(Output('start-time-section', 'hidden'), Input('weather-on', 'value'))
def toggle_start_time(weather_on):
    return not weather_on


@app.callback(Output('cda-flat', 'value'), Output('cda-up', 'value'), Output('cda-down', 'value'),
              Output('rr-value', 'value', allow_duplicate=True), Input('rider-preset', 'value'), State('rr-type', 'value'),
              prevent_initial_call=True)
def apply_rider_preset(preset, rr_type):
    values = RIDER_PRESETS[preset]
    return *values['cda'], round(values['crr'] * PRR_PER_CRR, 1) if rr_type == "Prr" else values['crr']


@app.callback(Output('rr-value', 'value'), Output('rr-value', 'step'), Output('rr-label', 'children'), Output('rr-hint', 'children'),
              Input('rr-type', 'value'), State('rr-value', 'value'), prevent_initial_call=True)
def convert_rolling_resistance(rr_type, value):
    """Convert the entered value when switching between Crr and Prr."""
    if rr_type == "Prr":
        return (round(value * PRR_PER_CRR, 1) if value is not None else None, 0.1, "Rolling resistance power at 8 m/s (Prr), W",
                rolling_resistance_hint("Prr"))
    return (round(value / PRR_PER_CRR, 5) if value is not None else None, 0.0001, "Rolling resistance coefficient (Crr)",
            rolling_resistance_hint("Crr"))


for modal_id, button_id in (('about-modal', 'open-about'), ('sources-modal', 'open-sources'), ('legal-modal', 'open-legal')):
    app.callback(Output(modal_id, 'is_open'), Input(button_id, 'n_clicks'), prevent_initial_call=True)(lambda n_clicks: True)


def parse_time(text):
    """24 h time from 'HH:MM' (also '9:15', '0915', '9.15', '9h15'); None if invalid."""
    match = re.fullmatch(r'\s*(\d{1,2})[:.h]?(\d{2})\s*', text or '')
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return None
    return datetime.strptime(f'{match.group(1)}:{match.group(2)}', '%H:%M').time()


@app.callback(Output('start-time', 'invalid'), Input('start-time', 'value'))
def validate_start_time(text):
    return bool(text) and parse_time(text) is None


# "Try a sample route" exists twice (start page and rail); the start page button disappears with the first result
@app.callback(Output('sample-request', 'data'), Input('try-sample', 'n_clicks'), prevent_initial_call=True)
def request_sample_from_start_page(n_clicks):
    return time.time() if n_clicks else dash.no_update


@app.callback(Output('sample-request', 'data', allow_duplicate=True), Input('try-sample-rail', 'n_clicks'), prevent_initial_call=True)
def request_sample_from_rail(n_clicks):
    return time.time() if n_clicks else dash.no_update


JOB_ID = re.compile(r'[a-z0-9]{6,40}')

dash.clientside_callback(  # Start an analysis: new job id, show the progress panel, poll it
    """
    function (nClicks, sampleRequest) {
        const triggered = window.dash_clientside.callback_context.triggered;
        const example = triggered.length > 0 && triggered[0].prop_id.startsWith('sample-request');
        const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
        return [{id: id, example: example}, false, false, 'Starting the analysis'];
    }
    """,
    Output('job', 'data'), Output('progress-poll', 'disabled', allow_duplicate=True), Output('progress', 'hidden', allow_duplicate=True),
    Output('progress', 'children', allow_duplicate=True),
    Input('analyze', 'n_clicks'), Input('sample-request', 'data'),
    prevent_initial_call=True,
)


@app.callback(Output('progress', 'children'), Input('progress-poll', 'n_intervals'), State('job', 'data'), prevent_initial_call=True)
def show_progress(n_intervals, job):
    status = PROGRESS.status(job['id']) if job and JOB_ID.fullmatch(str(job.get('id', ''))) else None
    return progress_panel(*status) if status else dash.no_update


@app.callback(
    Output('results', 'children'), Output('analysis-key', 'data'),
    Output('weather-on', 'value'), Output('start-date', 'date'), Output('start-time', 'value'), Output('climbs', 'value'),
    Output('progress-poll', 'disabled'), Output('progress', 'hidden'),
    Input('job', 'data'),
    State('input-method', 'value'), State('upload', 'contents'), State('upload', 'filename'), State('url', 'value'),
    State('course-rename', 'value'), State('course-name', 'value'),
    State('power-flat', 'value'), State('power-up', 'value'), State('power-down', 'value'),
    State('weight', 'value'), State('cda-flat', 'value'), State('cda-up', 'value'), State('cda-down', 'value'),
    State('rr-type', 'value'), State('rr-value', 'value'), State('climbs', 'value'),
    State('weather-on', 'value'), State('start-date', 'date'), State('start-time', 'value'), State('theme', 'data'),
    prevent_initial_call=True,
)
def run_analysis(job, *inputs):
    if not job or not JOB_ID.fullmatch(str(job.get('id', ''))):
        return (dash.no_update,) * 6 + (True, True)
    PROGRESS.start(job['id'])
    try:
        return analysis_outputs(bool(job.get('example')), *inputs) + (True, True)  # Done: stop polling, hide the progress panel
    finally:
        PROGRESS.finish()


def analysis_outputs(example, input_method, upload_contents, upload_filename, url, course_rename, course_name,
                     power_flat, power_up, power_down, weight, cda_flat, cda_up, cda_down,
                     rr_type, rr_value, climbs, weather_on, start_date, start_time, theme):
    """Outputs of run_analysis except for the progress panel: results, analysis key and the rail values for the example."""
    unchanged = (dash.no_update,) * 4

    def error(message):
        return (html.Div(dbc.Alert(message, color='danger'), className='ao-results'), None, *unchanged)

    if example:  # Sample route: the stage day and climbs, with the rider parameters as entered
        upload_contents, url, climbs, weather_on = None, None, True, True
        start_date, start_time = EXAMPLE_START.date().isoformat(), EXAMPLE_START.strftime('%H:%M')
        rail_update = (True, start_date, start_time, True)
    else:
        rail_update = unchanged
        if input_method == "Upload file":
            url = None
            if not upload_contents:
                return error("Please upload a route file or enter a RideWithGPS link first.")
        else:
            upload_contents = None
            if not url:
                return error("Please upload a route file or enter a RideWithGPS link first.")

    values = [power_flat, power_up, power_down, weight, cda_flat, cda_up, cda_down, rr_value]
    if any(value is None for value in values):
        return error("Please fill in all rider parameters.")

    timestamp = 0
    if weather_on:
        if not start_date or parse_time(start_time) is None:
            return error("Please enter the start date and time (24 h, HH:MM) for the weather forecast.")
        start = datetime.combine(date.fromisoformat(start_date), parse_time(start_time))
        timestamp = int(time.mktime(start.timetuple()))  # Local time, as on the command line

    settings = dict(power_flat=power_flat, power_up=power_up, power_down=power_down, weight=weight,
                    cda_flat=cda_flat, cda_up=cda_up, cda_down=cda_down, rr_type=rr_type,
                    crr=rr_value if rr_type == "Crr" else rr_value / PRR_PER_CRR, climbs=climbs, start_time=timestamp,
                    weather_file=str(EXAMPLE_WEATHER) if example and EXAMPLE_WEATHER.exists() else None)

    key = uuid.uuid4().hex
    route_path = None
    data = None
    if not ANALYSIS_SLOTS.acquire(blocking=False):
        print("- Waiting for a free slot (other analyses are running)")
        ANALYSIS_SLOTS.acquire()
    try:
        cleanup_work_dir()
        route_path, file_stem = save_route(key, upload_contents, upload_filename, url, example=example)
        args, data = analyze(route_path, settings)
        route_path.unlink(missing_ok=True)  # The route itself is not kept
        print("- Writing TCX, GPX and FIT files")
        write_outputs(key, data, course_name if course_rename else None)
        (WORK_DIR / key / 'name.txt').write_text(file_stem)
        log_analysis(data, success=True)
        print("- Preparing the results")
        layout = results_layout(key, args, data, settings, theme=theme if theme in plot_interactive.THEMES else 'light')
        return (layout, key, *rail_update)
    except AnalysisError as e:
        shutil.rmtree(WORK_DIR / key, ignore_errors=True)
        return error(str(e))
    except Exception as e:
        shutil.rmtree(WORK_DIR / key, ignore_errors=True)
        log_analysis(data, success=False)
        return error(f"An error occurred during the analysis: {e}")
    finally:
        ANALYSIS_SLOTS.release()
        if route_path is not None:
            route_path.unlink(missing_ok=True)


@app.callback(Output('download', 'data'), Output('download-message', 'children'),
              Input('download-tcx-button', 'n_clicks'), Input('download-gpx-button', 'n_clicks'), Input('download-fit-button', 'n_clicks'),
              State('analysis-key', 'data'), prevent_initial_call=True)
def download(tcx_clicks, gpx_clicks, fit_clicks, key):
    # Dash also calls this when a new analysis inserts the buttons (n_clicks 0): only react to real clicks
    clicks = {'download-tcx-button': tcx_clicks, 'download-gpx-button': gpx_clicks, 'download-fit-button': fit_clicks}
    if not clicks.get(dash.ctx.triggered_id):
        return dash.no_update, dash.no_update
    suffix = {'download-gpx-button': '.gpx', 'download-fit-button': '.fit'}.get(dash.ctx.triggered_id, '.tcx')
    folder = WORK_DIR / key if key and re.fullmatch(r'[0-9a-f]{32}', key) else None
    if folder is None or not (folder / f'route{suffix}').exists():
        return dash.no_update, dbc.Alert("The processed files are no longer available (deleted after 30 minutes). Please analyze the route again.",
                                         color='warning', className='mt-2 mb-0')
    name = (folder / 'name.txt').read_text()
    return dcc.send_file(str(folder / f'route{suffix}'), filename=f'{name}_processed{suffix}'), None


dash.clientside_callback(  # System light/dark setting at page load, so the figures are built in the right colors
    dash.ClientsideFunction(namespace='aeroracle', function_name='systemTheme'),
    Output('theme', 'data'),
    Input('theme', 'id'),
)

dash.clientside_callback(  # Default start: the next full or half hour on the visitor's clock (next day after midnight)
    """
    function (nIntervals) {
        const start = new Date();
        start.setSeconds(0, 0);
        start.setMinutes(start.getMinutes() < 30 ? 30 : 60);
        const pad = (n) => String(n).padStart(2, '0');
        return [`${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}`, `${pad(start.getHours())}:${pad(start.getMinutes())}`];
    }
    """,
    Output('start-date', 'date', allow_duplicate=True), Output('start-time', 'value', allow_duplicate=True),
    Input('page-loaded', 'n_intervals'),
    prevent_initial_call=True,
)

dash.clientside_callback(  # "Color by" menu of the route card, in the browser
    dash.ClientsideFunction(namespace='aeroracle', function_name='recolorMap'),
    Output('map-color-dummy', 'data'),
    Input('map-color', 'value'), State('map-color-options', 'data'),
)

dash.clientside_callback(
    dash.ClientsideFunction(namespace='aeroracle', function_name='hoverSync'),
    Output('hover-sync-dummy', 'data'),
    Input('route-map', 'figure'), Input('route-profile', 'figure'),
)


if __name__ == '__main__':
    app.run(debug=False)
