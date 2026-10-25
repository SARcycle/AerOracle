"""
Store the hourly weather of the example route's ride day, so that the example runs without weather API calls.

Requests Open-Meteo once for points every 5 km along the route and writes <route>_weather.json, read by
weather.stored_samples(). Run from the project folder:

    uv run python examples/build_example_weather.py
"""
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import openmeteo_requests
from openmeteo_sdk.Variable import Variable

ROUTE = Path(__file__).parent / 'tdf2022_stage12.json'
RIDE_DATE = '2022-07-14'
SPACING_KM = 5
VARIABLES = {  # Open-Meteo hourly variable: (SDK variable, altitude or None)
    'temperature_2m': (Variable.temperature, 2),
    'wind_direction_10m': (Variable.wind_direction, 10),
    'wind_speed_10m': (Variable.wind_speed, 10),
    'precipitation': (Variable.precipitation, None),
    'sunshine_duration': (Variable.sunshine_duration, None),
    'relative_humidity_2m': (Variable.relative_humidity, None),
    'surface_pressure': (Variable.surface_pressure, None),
}


def main():
    points = json.loads(ROUTE.read_text())['route']['track_points']
    distance_km = np.array([p['d'] for p in points]) / 1000
    targets = np.arange(0, distance_km[-1], SPACING_KM).tolist() + [distance_km[-1]]
    picked = sorted({int(np.argmin(np.abs(distance_km - km))) for km in targets})
    locations = [dict(lat=points[i]['y'], lon=points[i]['x'], elevation=points[i]['e'], km=round(float(distance_km[i]), 2)) for i in picked]

    responses = openmeteo_requests.Client().weather_api('https://archive-api.open-meteo.com/v1/archive', params={
        'latitude': [l['lat'] for l in locations], 'longitude': [l['lon'] for l in locations],
        'elevation': [l['elevation'] for l in locations], 'start_date': RIDE_DATE, 'end_date': RIDE_DATE,
        'hourly': list(VARIABLES), 'wind_speed_unit': 'ms', 'temperature_unit': 'celsius', 'timezone': 'auto',
    })

    hourly = {name: [] for name in VARIABLES}
    for response in responses:
        data = response.Hourly()
        variables = [data.Variables(i) for i in range(data.VariablesLength())]
        for name, (kind, altitude) in VARIABLES.items():
            variable = next(v for v in variables if v.Variable() == kind and (altitude is None or v.Altitude() == altitude))
            hourly[name].append(np.round(variable.ValuesAsNumpy().astype(float), 2).tolist())
    times = np.arange(responses[0].Hourly().Time(), responses[0].Hourly().TimeEnd(), responses[0].Hourly().Interval()).tolist()

    output = ROUTE.with_name(ROUTE.stem + '_weather.json')
    output.write_text(json.dumps(dict(
        source='Open-Meteo archive API', created=datetime.now().strftime('%Y-%m-%d'), date=RIDE_DATE, interval_s=3600,
        time=times, locations=locations, hourly=hourly), separators=(',', ':')))
    print(f'{output.name}: {len(locations)} locations x {len(times)} hours')


if __name__ == '__main__':
    main()
