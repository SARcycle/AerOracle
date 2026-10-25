import numpy as np
import polars as pl
import requests
from datetime import datetime

import utilities


def visualCrossingRequest(lat, lon, forecast_time, api_key):
    """Weather sample (dict with weather.SAMPLE_COLUMNS keys) at one location; None if the request failed."""
    import weather

    baseurl = 'https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline'
    url = baseurl + '/{},{}/{}?key={}&unitGroup=metric&include=obs,fcst,stats,histfcst,current&options=nonulls'.format(
        lat, lon, datetime.fromtimestamp(forecast_time).strftime('%Y-%m-%dT%H:%M:%S'), api_key)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.77 Safari/537.36"}

    json_forecast = requests.get(url, headers=headers)

    if json_forecast.status_code != 200:
        return None

    forecast = parseForecastJSON(json_forecast)
    forecast_dates = forecast['datetimeEpoch']
    if len(forecast_dates) > 1:  # If variables are from hourly forecasts
        interval = forecast_dates[1] - forecast_dates[0]
    else:  # If variables are from currentConditions
        interval = 3600

    sample = dict.fromkeys(weather.SAMPLE_COLUMNS, np.nan)
    at_time = lambda key: weather.value_at(forecast_dates, forecast[key], forecast_time)  # Finding forecast closest to desired time
    sample['temperature_c'] = at_time('temp')
    sample['wind_direction_deg'] = at_time('winddir')
    sample['wind_speed_ms'] = at_time('windspeed') / 3.6  # Because given in km/h
    sample['precipitation_mm_s'] = at_time('precip') / interval
    sample['uv_index'] = at_time('uvindex')
    return sample


def getVisualCrossingForecast(lat, lon, forecast_time):
    """
    Weather at the forecast points.
    lat, lon [°], forecast_time [Unix s]: arrays with one entry per forecast point.
    Returns a pl.DataFrame with one row per forecast point (columns: see weather.SAMPLE_COLUMNS).
    """
    from multiprocessing.dummy import Pool
    from itertools import repeat
    import weather

    print("\t- Getting Weather Data ({0:.0f} queries from VisualCrossing)".format(len(lat)))
    api_key = utilities.get_credentials('VisualCrossing')[1]
    if not api_key:
        raise RuntimeError('VisualCrossing: no API key in credentials.json')

    forecast_time_rounded = np.multiply(np.round(np.divide(forecast_time, 3600)), 3600)

    numthreads = min(len(lat), 2)

    samples = Pool(numthreads).starmap(visualCrossingRequest, zip(lat, lon, forecast_time_rounded, repeat(api_key)))
    if not all(sample is not None for sample in samples):
        raise RuntimeError('VisualCrossing: request unsuccessful')
    return pl.DataFrame(samples, schema=weather.SAMPLE_COLUMNS)


FIELDS = ('datetimeEpoch', 'winddir', 'windspeed', 'temp', 'precip', 'uvindex', 'humidity')


def parseForecastJSON(jsonObject):
    """Forecast values {field: np.ndarray} for the FIELDS, NaN where missing."""
    try:  # In case the number of requests is exceeded
        j = jsonObject.json()

        if 'currentConditions' in j: # If precise point in time was given
            entries = [j['currentConditions']]
        else: # If request was on hourly basis
            entries = j['days'][0]['hours']
    except Exception:
        entries = [{}] * 24

    return {field: np.array([entry.get(field, np.nan) for entry in entries], dtype=float) for field in FIELDS}
