import numpy as np
import polars as pl
from datetime import datetime
import openmeteo_requests
from openmeteo_sdk.Variable import Variable
import requests_cache
from retry_requests import retry
import os, sys

import utilities

try:  # Usage statistics with resource use of the API calls (usage.db)
    from resource_monitor import track_resources
    from tracker_with_resources import track_api_call_with_resources, log_resource_usage
    import tracker
    TRACKING_ENABLED = True
except ImportError:
    TRACKING_ENABLED = False


def getOpenMeteoForecast(lat, lon, elevation, forecast_time):
    """
    Weather at the forecast points.
    lat, lon [°], elevation [m], forecast_time [Unix s]: arrays with one entry per forecast point.
    Returns a pl.DataFrame with one row per forecast point (columns: see weather.SAMPLE_COLUMNS).
    """
    import weather

    # Setup the Open-Meteo API client with cache and retry on error
    cache_session = requests_cache.CachedSession('.cache', expire_after=3600, allowable_methods=('GET', 'POST'))
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    openmeteo = openmeteo_requests.Client(session=retry_session)

    print("\t- Getting Weather Data ({0:.0f} queries from Open-Meteo)".format(len(lat)))

    now_ts = datetime.timestamp(datetime.now())
    timezone = utilities.timezone_at(lat[0], lon[0])  # Dates are local dates of the locations (timezone "auto")
    cutoff_archive    = datetime(2024, 1, 1).timestamp()   # before 2024 → archive API
    cutoff_hist_fcst  = now_ts - 2 * 86400                 # 2024–2 days ago → historical forecast API
                                                           # within last 2 days → forecast API

    if np.max(forecast_time) < cutoff_archive:
        # Dates before 2024: ERA5 / archive API
        url = "https://archive-api.open-meteo.com/v1/archive"
        specific_params = {
            "start_date": datetime.fromtimestamp(np.min(forecast_time), timezone).strftime("%Y-%m-%d"),
            "end_date":   datetime.fromtimestamp(np.max(forecast_time), timezone).strftime("%Y-%m-%d"),
            "hourly": ["temperature_2m", "wind_direction_10m", "wind_speed_10m",
                       "precipitation", "sunshine_duration", "relative_humidity_2m",
                       "surface_pressure"]
        }

    elif np.max(forecast_time) < cutoff_hist_fcst:
        # 2024 until 2 days before today: historical forecast API
        url = "https://historical-forecast-api.open-meteo.com/v1/forecast"
        specific_params = {
            "start_date": datetime.fromtimestamp(np.min(forecast_time), timezone).strftime("%Y-%m-%d"),
            "end_date":   datetime.fromtimestamp(np.max(forecast_time), timezone).strftime("%Y-%m-%d"),
            "hourly": ["temperature_2m", "wind_direction_10m", "wind_speed_10m",
                       "precipitation", "sunshine_duration", "relative_humidity_2m",
                       "surface_pressure"]
        }

    else:
        # Within last 2 days / future: forecast API
        url = "https://api.open-meteo.com/v1/forecast"
        specific_params = {
            "past_days":     6,
            "forecast_days": 7,
            "hourly": ["temperature_2m", "wind_direction_10m", "wind_speed_10m",
                       "precipitation", "sunshine_duration", "relative_humidity_2m",
                       "surface_pressure", "uv_index"]
        }
        # Within ±1 day of now: also fetch 15-min resolution
        if np.min(forecast_time) > now_ts - 86400 and np.max(forecast_time) < now_ts + 86400:
            specific_params["minutely_15"]         = ["temperature_2m", "wind_direction_10m",
                                                       "wind_speed_10m", "precipitation",
                                                       "sunshine_duration", "relative_humidity_2m"]
            specific_params["past_minutely_15"]    = 96
            specific_params["forecast_minutely_15"] = 96

    params = {
        "latitude":         lat,
        "longitude":        lon,
        "elevation":        np.round(elevation),
        "wind_speed_unit":  "ms",
        "temperature_unit": "celsius",
        "timeformat":       "iso8601",
        "timezone":         "auto"
    }

    if TRACKING_ENABLED:
        with track_resources() as monitor:
            try:
                responses = openmeteo.weather_api(url, params={**params, **specific_params}, method='POST')  # Body instead of URL: no length limit for long routes
                api_success = True
            except Exception as e:
                api_success = False
                raise
            finally:
                metrics = monitor.get_metrics()

                # Forecast or historical request
                if specific_params.get("start_date") and specific_params.get("end_date"):
                    from datetime import datetime as _dt
                    _d0 = _dt.strptime(specific_params["start_date"], "%Y-%m-%d")
                    _d1 = _dt.strptime(specific_params["end_date"],   "%Y-%m-%d")
                    _time_range_days = (_d1 - _d0).days or 1
                else:
                    _time_range_days = (specific_params.get("past_days") or 0) + (specific_params.get("forecast_days") or 0) or 1

                tracker_params = {
                    "latitude": list(lat),
                    "longitude": list(lon),
                    "hourly": specific_params.get("hourly", []),
                    "minutely_15": specific_params.get("minutely_15", []),
                    "forecast_days": specific_params.get("forecast_days"),
                    "past_days": specific_params.get("past_days"),
                    "start_date": specific_params.get("start_date"),
                    "end_date": specific_params.get("end_date"),
                    "time_range_days": _time_range_days
                }
                api_call_id = track_api_call_with_resources(tracker, tracker_params, success=api_success)
                log_resource_usage(metrics, operation_type="api_call", api_call_id=api_call_id)
                print(f"\t  └─ API call: {metrics.duration_seconds:.2f}s | CPU: {metrics.cpu_max:.0f}% | RAM: {metrics.memory_max_mb:.0f}MB")
                if metrics.cpu_max > 80:
                    print("\t     Warning: high CPU usage")
                if metrics.memory_percent_max > 80:
                    print("\t     Warning: high RAM usage")
    else:
        responses = openmeteo.weather_api(url, params={**params, **specific_params}, method='POST')  # Body instead of URL: no length limit for long routes

    samples = []
    for idx, response_location in enumerate(responses): # Iterate through all the locations requested
        if hasattr(response_location, 'Minutely15') and response_location.Minutely15() is not None and np.min(forecast_time)>response_location.Minutely15().Time() and np.max(forecast_time)<response_location.Minutely15().TimeEnd(): # If within range of 15-minutely forecasts
            response = response_location.Minutely15()
        else:
            response = response_location.Hourly()
        forecast_dates = np.arange(response.Time(), response.TimeEnd(), response.Interval())
        variables = list(map(lambda i: response.Variables(i), range(0, response.VariablesLength())))
        variable = lambda kind, altitude=None: next(filter(lambda x: x.Variable() == kind and (altitude is None or x.Altitude() == altitude), variables)).ValuesAsNumpy()
        interval = response.Interval() or 1
        sample = dict.fromkeys(weather.SAMPLE_COLUMNS, np.nan)
        try:
            at_time = lambda values, dates=forecast_dates: weather.value_at(dates, values, forecast_time[idx])
            sample['temperature_c'] = at_time(variable(Variable.temperature, 2))
            sample['wind_direction_deg'] = at_time(variable(Variable.wind_direction, 10))
            sample['wind_speed_ms'] = at_time(variable(Variable.wind_speed, 10))
            sample['precipitation_mm_s'] = at_time(variable(Variable.precipitation)) / interval  # Value is in mm for the previous interval --> division for rate
            sample['sunshine_fraction'] = at_time(variable(Variable.sunshine_duration)) / interval  # Value is in seconds for the previous interval --> division for fraction
            sample['relative_humidity'] = at_time(variable(Variable.relative_humidity)) / 100  # Convert value to interval (0.0-1.0)

            # Surface pressure and UV index are only available as hourly values (and not in the archive)
            hourly = response_location.Hourly()
            hourly_variables = list(map(lambda i: hourly.Variables(i), range(0, hourly.VariablesLength())))
            forecast_dates_hourly = np.arange(hourly.Time(), hourly.TimeEnd(), hourly.Interval())
            hourly_variable = lambda kind: next(filter(lambda x: x.Variable() == kind, hourly_variables)).ValuesAsNumpy()
            if hourly.VariablesLength() >= 7:
                sample['surface_pressure_hpa'] = at_time(hourly_variable(Variable.surface_pressure), forecast_dates_hourly)
            if hourly.VariablesLength() >= 8:
                sample['uv_index'] = at_time(hourly_variable(Variable.uv_index), forecast_dates_hourly)
        except Exception as e:  # if all nan
            exc_type, exc_obj, exc_tb = sys.exc_info()
            fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            print(f'Error ({fname}@{exc_tb.tb_lineno}): {e}')
            sample = dict.fromkeys(weather.SAMPLE_COLUMNS, np.nan)
        samples.append(sample)

    return pl.DataFrame(samples, schema=weather.SAMPLE_COLUMNS)
