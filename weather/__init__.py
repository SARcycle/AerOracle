"""
Weather along the route: requests forecasts every 10 min of ride time from the selected provider
(with fallback), interpolates them to all track points and summarizes them.
"""
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import polars as pl
from scipy.interpolate import interp1d

import config
from parameters import WeatherProvider
from weather import meteoblue, openmeteo, visualcrossing

FORECAST_INTERVAL_S = 600  # Request a forecast every 10 min of ride time ...
FORECAST_INTERVAL_STEP_S = 300  # ... longer by 5 min steps while the route needs more than config.MAX_FORECAST_LOCATIONS
FORECAST_WIND_HEIGHT_M = 10  # Height of the forecast wind above ground
RIDER_HEIGHT_M = 1.2  # Height of the rider's torso, where the wind acts (Martin et al. 1998 measured it there)

# Columns of the provider results: one row per forecast point, NaN where a provider has no value
SAMPLE_COLUMNS = {
    'temperature_c': pl.Float64,         # Air temperature [°C]
    'wind_direction_deg': pl.Float64,    # Direction the wind blows from [°]
    'wind_speed_ms': pl.Float64,         # Wind speed [m/s]
    'precipitation_mm_s': pl.Float64,    # Precipitation rate [mm/s]
    'sunshine_fraction': pl.Float64,     # Share of the forecast interval with sunshine [0.0-1.0]
    'relative_humidity': pl.Float64,     # Relative humidity [0.0-1.0]
    'surface_pressure_hpa': pl.Float64,  # Surface pressure [hPa]
    'uv_index': pl.Float64,              # UV index [-]
}

# Columns interpolated to all track points and the interpolation used
TRACK_COLUMNS = {
    'temperature_c': 'slinear',
    'wind_direction_deg': 'quadratic',
    'wind_speed_ms': 'quadratic',
    'relative_humidity': 'slinear',
    'surface_pressure_hpa': 'slinear',
    'precipitation_mm_s': 'slinear',
}


@dataclass(slots=True)
class WeatherSummary:
    provider: WeatherProvider
    sunshine_fraction: float | None = None  # Share of the ride with sunshine [0.0-1.0]
    uv_index_max: float | None = None       # Maximum UV index during the ride
    precipitation_mm: float | None = None   # Total precipitation during the ride [mm]
    forecast_interval_s: int = FORECAST_INTERVAL_S  # Ride time between two forecast locations


def value_at(dates, values, time):
    """Value with the time stamp closest to time, ignoring missing values (NaN if there is none)."""
    if values is None:
        return np.nan
    dates, values = np.asarray(dates, dtype=float), np.asarray(values, dtype=float)
    valid = ~np.isnan(values) & ~np.isnan(dates)
    if not valid.any():
        return np.nan
    return values[valid][np.argmin(np.abs(dates[valid] - time))]


def forecast_points(track, start_time):
    """
    Indices of the track points with a forecast request, their (Unix) forecast times and the interval [s] between them:
    every FORECAST_INTERVAL_S of ride time, longer in FORECAST_INTERVAL_STEP_S steps for long rides, so that there are
    at most config.MAX_FORECAST_LOCATIONS (start and finish included; limits the load on the weather APIs).
    """
    ride_time = track['timestamp']
    # Locations: one every interval from the start (floor(T / interval) + 1) plus the finish, so at most N for
    # interval >= T / (N - 2); rounded up to the step
    duration = ride_time[-1] - ride_time[0]
    steps = math.ceil(duration / (config.MAX_FORECAST_LOCATIONS - 2) / FORECAST_INTERVAL_STEP_S)
    interval = max(FORECAST_INTERVAL_S, steps * FORECAST_INTERVAL_STEP_S)
    forecast_interval = (ride_time - ride_time[0]) % interval
    forecast_idx = np.where(np.diff(forecast_interval) < 0)[0] + 1
    forecast_idx = np.unique(np.append(np.insert(forecast_idx, 0, 0), len(forecast_interval) - 1))
    if start_time == 0:
        start_time = ride_time[0]
    forecast_time = (start_time + (ride_time[forecast_idx] - ride_time[0])).astype(int)
    return forecast_idx, forecast_time, interval


# Stored hourly weather (examples/build_example_weather.py): Open-Meteo variable -> (sample column, conversion)
STORED_COLUMNS = {
    'temperature_2m': ('temperature_c', lambda v: v),
    'wind_direction_10m': ('wind_direction_deg', lambda v: v),
    'wind_speed_10m': ('wind_speed_ms', lambda v: v),
    'precipitation': ('precipitation_mm_s', lambda v: v / 3600),     # mm per hour -> rate
    'sunshine_duration': ('sunshine_fraction', lambda v: v / 3600),  # seconds per hour -> fraction
    'relative_humidity_2m': ('relative_humidity', lambda v: v / 100),
    'surface_pressure': ('surface_pressure_hpa', lambda v: v),
}


def stored_samples(path, lat, lon, forecast_time):
    """Weather samples from a stored hourly weather file: nearest stored location, closest hour."""
    stored = json.loads(open(path).read())
    loc_lat = np.array([l['lat'] for l in stored['locations']])
    loc_lon = np.array([l['lon'] for l in stored['locations']])
    times = np.array(stored['time'], dtype=float)
    kx = np.cos(np.deg2rad(np.mean(loc_lat)))
    samples = []
    for la, lo, t in zip(lat, lon, forecast_time):
        location = int(np.argmin((loc_lat - la) ** 2 + ((loc_lon - lo) * kx) ** 2))
        sample = dict.fromkeys(SAMPLE_COLUMNS, np.nan)
        for variable, (column, convert) in STORED_COLUMNS.items():
            sample[column] = convert(value_at(times, stored['hourly'][variable][location], t))
        samples.append(sample)
    return pl.DataFrame(samples, schema=SAMPLE_COLUMNS)


def request_forecast(provider, track, forecast_idx, forecast_time):
    lat, lon, elevation = track['lat'][forecast_idx], track['lon'][forecast_idx], track['elevation_m'][forecast_idx]
    if provider == WeatherProvider.OPEN_METEO:
        samples = openmeteo.getOpenMeteoForecast(lat, lon, elevation, forecast_time)
    elif provider == WeatherProvider.VISUAL_CROSSING:
        samples = visualcrossing.getVisualCrossingForecast(lat, lon, forecast_time)
    elif provider == WeatherProvider.METEOBLUE:
        samples = meteoblue.getMeteoBlueForecast(lat, lon, elevation, forecast_time)
    for column in ('temperature_c', 'wind_direction_deg', 'wind_speed_ms'):
        if samples[column].is_nan().all():
            raise ValueError(f'No {column} values received from {provider.name}')
    return samples


def print_error(e):
    exc_type, exc_obj, exc_tb = sys.exc_info()
    fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
    print(f'Error ({fname}@{exc_tb.tb_lineno}): {e}')


def get_samples(parameters, track, forecast_idx, forecast_time):
    """Request the forecasts from the selected provider, falling back to another one on errors."""
    if getattr(parameters, 'weather_file', None):  # Stored weather (example route): no API calls
        print(f'\t- Using stored weather ({os.path.basename(parameters.weather_file)})')
        setattr(parameters, 'weather', WeatherProvider.OPEN_METEO)
        return stored_samples(parameters.weather_file, track['lat'][forecast_idx], track['lon'][forecast_idx], forecast_time)
    if parameters.weather == WeatherProvider.METEOBLUE:
        if parameters.time > datetime.timestamp(datetime.now().replace(microsecond=0, second=0, minute=0, hour=0)):  # MeteoBlue only for rides after 00:00 today
            order = [WeatherProvider.METEOBLUE, WeatherProvider.OPEN_METEO]
        else:
            order = [WeatherProvider.OPEN_METEO, WeatherProvider.VISUAL_CROSSING]
    elif parameters.weather == WeatherProvider.VISUAL_CROSSING:
        order = [WeatherProvider.VISUAL_CROSSING, WeatherProvider.OPEN_METEO]
    else:
        order = [WeatherProvider.OPEN_METEO, WeatherProvider.VISUAL_CROSSING]

    try:
        samples = request_forecast(order[0], track, forecast_idx, forecast_time)
        provider = order[0]
    except Exception as e:  # In case something goes wrong with the first provider
        print_error(e)
        samples = request_forecast(order[1], track, forecast_idx, forecast_time)
        provider = order[1]
    setattr(parameters, 'weather', provider)
    return samples


def interpolate_to_track(track, forecast_idx, samples):
    """Interpolate the forecast samples to all track points (by point index) and add them as columns."""
    n = len(track)
    ride_time = track['timestamp']
    interpvec_temp = np.linspace(ride_time[0], ride_time[-1], n)
    columns = {}
    for column, kind in TRACK_COLUMNS.items():
        values = samples[column].to_numpy(writable=True)
        known = ~np.isnan(values)
        if not known.any():
            continue
        if column == 'wind_direction_deg':  # Avoid jumps between 359° and 0°
            values[known] = np.unwrap(values[known], period=360)

        at_points = np.full(n, np.nan)
        if len(values) == 2:  # Two forecasts only: add their mean in the middle (needed for quadratic interpolation)
            at_points[0], at_points[n // 2], at_points[-1] = values[0], (values[0] + values[-1]) / 2, values[-1]
        else:
            at_points[forecast_idx] = values
        if column == 'temperature_c':
            columns['is_forecast_point'] = ~np.isnan(at_points)

        known = ~np.isnan(at_points)
        at_points = interp1d(interpvec_temp[known], at_points[known], kind, fill_value="extrapolate")(interpvec_temp)
        columns[column] = at_points % 360 if column == 'wind_direction_deg' else at_points
    track.set(**columns)


def summarize(provider, samples, forecast_time):
    def finite(column):
        values = samples[column].to_numpy()
        return values if not np.isnan(values).all() else None

    sunshine, uv_index, precipitation = finite('sunshine_fraction'), finite('uv_index'), finite('precipitation_mm_s')
    return WeatherSummary(
        provider=provider,
        sunshine_fraction=float(np.nanmean(sunshine)) if sunshine is not None else None,
        uv_index_max=float(np.nanmax(uv_index)) if uv_index is not None else None,
        # Multiplying precipitation rate with ride time for the forecast
        precipitation_mm=float(np.nansum(precipitation[1:] * np.diff(forecast_time))) if precipitation is not None else None,
    )


def add_weather(assets, parameters) -> bool:
    """
    Add the weather columns (TRACK_COLUMNS, 'is_forecast_point') to assets.track and set assets.weather_summary.
    Returns False (and sets parameters.time = False) if no weather data could be retrieved.
    """
    try:
        forecast_idx, forecast_time, interval = forecast_points(assets.track, parameters.time)
        samples = get_samples(parameters, assets.track, forecast_idx, forecast_time)
        interpolate_to_track(assets.track, forecast_idx, samples)
        # Power law with an effective exponent (terrain roughness and shelter): wind at rider height
        assets.track.set(wind_speed_ms=assets.track['wind_speed_ms'] * (RIDER_HEIGHT_M / FORECAST_WIND_HEIGHT_M) ** config.WIND_SHEAR_EXPONENT)
    except Exception as e:
        print('\t- Retrieval of weather information not successful. Continuing without weather information.')
        setattr(parameters, 'time', False)
        print_error(e)
        return False
    setattr(assets, 'weather_summary', summarize(parameters.weather, samples, forecast_time))
    assets.weather_summary.forecast_interval_s = interval
    return True
