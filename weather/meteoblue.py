import numpy as np
import polars as pl
from datetime import datetime as dt
import sys, os

import requests

import utilities


def meteoBlueRequest(lat, lon, ele, forecast_time, api_key):
    """Weather sample (dict with weather.SAMPLE_COLUMNS keys) at one location."""
    import weather

    baseurl = 'https://my.meteoblue.com/packages/basic-15min?format=json&'
    url = baseurl + f'apikey={api_key}&lat={lat}&lon={lon}&asl={ele}&windspeed=ms-1&forecast_days=7&history_days=4'
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.77 Safari/537.36"}
    json_forecast = requests.get(url, headers=headers)
    forecast_dates, winddir, windspeed, temp, success = parseForecastJSON(json_forecast, utilities.timezone_at(lat, lon))

    sample = dict.fromkeys(weather.SAMPLE_COLUMNS, np.nan)
    # Finding forecast closest to desired time
    sample['temperature_c'] = weather.value_at(forecast_dates, temp, forecast_time)
    sample['wind_direction_deg'] = weather.value_at(forecast_dates, winddir, forecast_time)
    sample['wind_speed_ms'] = weather.value_at(forecast_dates, windspeed, forecast_time)
    return sample


def getMeteoBlueForecast(lat, lon, elevation, forecast_time):
    """
    Weather at the forecast points.
    lat, lon [°], elevation [m], forecast_time [Unix s]: arrays with one entry per forecast point.
    Returns a pl.DataFrame with one row per forecast point (columns: see weather.SAMPLE_COLUMNS).
    """
    from multiprocessing.dummy import Pool
    from itertools import repeat
    import weather

    print("\t- Getting Weather Data ({0:.0f} queries from MeteoBlue)".format(len(lat)))
    api_key = utilities.get_credentials('Meteoblue')[1]
    if not api_key:
        raise RuntimeError('MeteoBlue: no API key in credentials.json')

    numthreads = min(len(lat), 2)

    try:
        samples = Pool(numthreads).starmap(meteoBlueRequest, zip(lat, lon, elevation, forecast_time, repeat(api_key)))
        return pl.DataFrame(samples, schema=weather.SAMPLE_COLUMNS)
    except Exception as e:
        exc_type, exc_obj, exc_tb = sys.exc_info()
        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
        print(f'Error ({fname}@{exc_tb.tb_lineno}): {e}')
        raise RuntimeError('MeteoBlue: request unsuccessful') from e


def parseForecastJSON(json_object, timezone):
    """Forecast values; the times in the JSON are local times of the location (timezone)."""
    j = json_object.json()
    if 'error' in j: # In case the number of requests is exceeded
        missing = np.full((24,), np.nan)
        return missing, missing, missing, missing, False

    data = j['data_xmin']
    forecast_dates = np.array([dt.strptime(time, '%Y-%m-%d %H:%M').replace(tzinfo=timezone).timestamp() for time in data['time']])
    winddir = np.array(data['winddirection'], dtype=float)
    windspeed = np.array(data['windspeed'], dtype=float)
    temp = np.array(data['temperature'], dtype=float)
    return forecast_dates, winddir, windspeed, temp, True
