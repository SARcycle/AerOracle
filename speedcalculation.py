import copy
import warnings
from typing import NamedTuple

import numpy as np
from scipy import signal

import utilities
import weather
from parameters import PerGradient, PowerSummary

RHO0 = 1.225  # Air density at sea level [kg/m³]
MAX_SPEED_MS = 80 / 3.6
DRIVETRAIN_EFFICIENCY = 0.98
np_exceptions = getattr(np, 'exceptions', np)  # Warning classes moved to np.exceptions in numpy 1.25 (removed from np in 2.0)


def __init__(parameters, assets):
    track = assets.track

    warnings.simplefilter(action='ignore', category=np_exceptions.RankWarning)
    warnings.simplefilter(action='ignore', category=FutureWarning)
    warnings.simplefilter(action='ignore', category=RuntimeWarning)
    warnings.filterwarnings('ignore', category=np_exceptions.VisibleDeprecationWarning) 

    delta_distance = np.insert(np.diff(track['distance_m']), 0, 0)
    delta_elevation = np.insert(np.diff(track['elevation_m']), 0, 0)
    gradient = np.nan_to_num(delta_elevation / delta_distance)

    gradient = np.clip(gradient, -.2, .2)
    for idx in range(5):
        gradient = signal.savgol_filter(gradient, 5, 2)
    gradient = np.clip(gradient, -.2, .2)

    track.set(delta_distance_m=delta_distance, delta_elevation_m=delta_elevation, gradient=gradient)

    assets = noWeatherAdjustment(assets, parameters)
    if parameters.time: # If no time is given -> no weather adjustment
        if weather.add_weather(assets, parameters):
            assets = weatherAdjustment(assets, parameters)
    assets = roadsinuosityadjustment(assets)

    return assets


def per_gradient(gradient, setting, threshold):
    """Value of a PerGradient setting for every point (uphill: gradient >= threshold, downhill: <= -threshold)."""
    values = np.full(len(gradient), setting.flat, dtype=float)
    values[gradient >= threshold] = setting.uphill
    values[gradient <= -threshold] = setting.downhill
    return values


def air_density_from_temperature(temperature, gravity, elevation):
    return RHO0 * (273.15 / (temperature + 273.15) * np.exp(-RHO0 * gravity * (elevation / 101325)))


def air_density_from_weather(temperature, relative_humidity, surface_pressure):
    # True air density based on relative humidity, temperature and surface pressure
    R_d = 287.058 # specific gas constant for dry air [J/(kg·K)]
    R_v = 461.495 # specific gas constant for water vapor [J/(kg·K)]
    temperature = temperature + 273.15 # Temperature in Kelvin
    surface_pressure = surface_pressure * 100 # Surface pressure in Pa
    p_sat = 0.61078*np.exp((17.27*(temperature-273.15))/(temperature-35.85)) # saturation vapor pressure
    p_v = relative_humidity*p_sat # vapor pressure of water
    p_d = surface_pressure - p_v # partial pressure of dry air
    return (p_d/(R_d*temperature))+(p_v/(R_v*temperature))


def cubic_roots_real_parts(a, b, c, d):
    """
    Real parts of the three roots of a*v³ + b*v² + c*v + d = 0 for arrays of coefficients (a != 0).
    Same values as np.real(np.roots([a, b, c, d])) per point; a complex pair shows up twice.
    Returns an array of shape (3, n).
    """
    a, b, c, d = np.broadcast_arrays(*(np.asarray(x, dtype=float) for x in (a, b, c, d)))
    shift = b / (3 * a)
    # Depressed cubic t³ + p*t + q = 0 with v = t - shift
    p = (3 * a * c - b ** 2) / (3 * a ** 2)
    q = (2 * b ** 3 - 9 * a * b * c + 27 * a ** 2 * d) / (27 * a ** 3)
    discriminant = (q / 2) ** 2 + (p / 3) ** 3

    roots = np.empty((3,) + a.shape)
    with np.errstate(invalid='ignore', divide='ignore'):
        # One real root and a complex pair (Cardano, written to avoid cancellation)
        sqrt_discriminant = np.sqrt(np.maximum(discriminant, 0))
        u = np.cbrt(np.where(q >= 0, -q / 2 - sqrt_discriminant, -q / 2 + sqrt_discriminant))
        t_real = np.where(u != 0, u - p / (3 * u), 0)
        # Three real roots (trigonometric solution)
        m = 2 * np.sqrt(np.maximum(-p / 3, 0))
        theta = np.arccos(np.clip(3 * q / (p * m), -1, 1)) / 3
        three_real = discriminant < 0
        for k in range(3):
            roots[k] = np.where(three_real, m * np.cos(theta - 2 * np.pi * k / 3), t_real if k == 0 else -t_real / 2) - shift
    return roots


def single_positive_root(roots):
    """Per point the only non-negative root, NaN if there is none or several."""
    non_negative = roots >= 0
    return np.where(non_negative.sum(axis=0) == 1, np.max(np.where(non_negative, roots, -np.inf), axis=0), np.nan)


def forward_fill(values, first=0):
    """Replace NaN by the previous valid value (first if there is none)."""
    values = np.asarray(values, dtype=float).copy()
    if np.isnan(values[0]):
        values[0] = first
    valid_idx = np.where(np.isnan(values), 0, np.arange(len(values)))
    return values[np.maximum.accumulate(valid_idx)]


def update_ride_time(start_time, delta_distance, speed):
    """Timestamp of every point when riding with speed, starting at start_time."""
    segment_time = delta_distance / speed
    segment_time[0] = start_time
    return np.cumsum(segment_time)


def speed_without_wind(parameters, track):
    """Speed [m/s] at every point without wind at 0 °C."""
    temperature = 0
    gradient = track['gradient']
    power = per_gradient(gradient, parameters.power, parameters.gradient_threshold)
    cda = per_gradient(gradient, parameters.cda, parameters.gradient_threshold) # Before: 0.32 / 0.42 / 0.28
    crr = parameters.crr # Before: 0.0033
    gravity = track['gravity_ms2']
    mass = parameters.weight * gravity
    air_density = air_density_from_temperature(temperature, gravity, np.nan_to_num(track['elevation_m']))

    roots = cubic_roots_real_parts((air_density / 2) * cda, 0,
                                   crr * mass * np.cos(np.arctan(gradient)) + mass * np.sin(np.arctan(gradient)),
                                   -1 * power * DRIVETRAIN_EFFICIENCY)
    speed = forward_fill(single_positive_root(roots))  # Keep previous speed if there is no unique solution
    return np.minimum(speed, MAX_SPEED_MS)


def speed_with_wind(parameters, track, speed_no_wind):
    """Speed [m/s] at every point with the weather columns (headwind_ms, air_density_kgm3) of track."""
    gradient = track['gradient']
    power = per_gradient(gradient, parameters.power, parameters.gradient_threshold)
    cda = per_gradient(gradient, parameters.cda, parameters.gradient_threshold) # Before: 0.32 / 0.42 / 0.28
    crr = parameters.crr  # Before: 0.0033
    mass = parameters.weight * track['gravity_ms2']
    headwind = track['headwind_ms']
    drag = (track['air_density_kgm3'] / 2) * cda
    roots = cubic_roots_real_parts(drag, 2 * headwind * drag,
                                   headwind ** 2 * drag + crr * mass * np.cos(np.arctan(gradient)) + mass * np.sin(np.arctan(gradient)),
                                   -1 * power * DRIVETRAIN_EFFICIENCY)

    # Choice of the root as before: the only non-negative one; otherwise, the only one faster (tail wind) /
    # slower (head wind) than without wind; otherwise keep the speed without wind
    candidates = np.where(headwind < 0, roots > speed_no_wind, roots <= speed_no_wind)
    speed = single_positive_root(roots)
    speed_wind = np.max(np.where(candidates, roots, -np.inf), axis=0)
    speed = np.where(np.isnan(speed) & (candidates.sum(axis=0) == 1), speed_wind, speed)
    speed = np.where(np.isnan(speed), speed_no_wind, speed)
    return np.minimum(speed, MAX_SPEED_MS)


def sinuosity_adjusted(speed, sinuosity):
    """Slow down fast (> 50 km/h) passages on winding roads."""
    return np.where((speed > 50/3.6) & (sinuosity >= 1.045), speed * 0.6, speed)


def noWeatherAdjustment(assets, parameters):
    print('- Calculating Rough Speeds (w/o Weather Data)')
    track = assets.track
    speed = speed_without_wind(parameters, track)

    timezone_offset = utilities.get_timezone_offset(assets, parameters)
    track.set(speed_ms=speed, timestamp=update_ride_time((parameters.time or 0) - timezone_offset, track['delta_distance_m'], speed))
    setattr(parameters, 'power_out', calculate_power_numbers(track['timestamp'], track['gradient'], parameters.power, parameters.gradient_threshold))

    return assets


def weatherAdjustment(assets, parameters):
    print('- Calculating Precise Speeds (w/ Weather Data)')
    track = assets.track
    track.set(headwind_ms=windBearingProjection(track))

    gravity = track['gravity_ms2']
    air_density = air_density_from_temperature(track['temperature_c'], gravity, np.nan_to_num(track['elevation_m']))
    if track.has('relative_humidity') and track.has('surface_pressure_hpa'):  # If air density can be calculated based on weather parameters
        humid_air_density = air_density_from_weather(track['temperature_c'], track['relative_humidity'], track['surface_pressure_hpa'])
        air_density = np.where(np.isnan(humid_air_density), air_density, humid_air_density)
    track.set(air_density_kgm3=air_density)

    speed = speed_with_wind(parameters, track, track['speed_ms'])

    timezone_offset = utilities.get_timezone_offset(assets, parameters)
    track.set(speed_ms=speed, timestamp=update_ride_time(parameters.time - timezone_offset, track['delta_distance_m'], speed))
    setattr(parameters, 'power_out', calculate_power_numbers(track['timestamp'], track['gradient'], parameters.power, parameters.gradient_threshold))
    track.set(yaw_angle_deg=calculate_yaw_angle(track))

    return assets


def roadsinuosityadjustment(assets):
    print("- Adjusting for Road Sinuosity")
    from pyproj import Geod

    track = assets.track
    lat, lon, distance = track['lat'], track['lon'], track['distance_m']
    # Path length vs. geodesic distance between the points 100 m before and after each point
    startidx = utilities.nearest_index(distance, distance - 100)
    endidx = utilities.nearest_index(distance, distance + 100)
    geodesic_distance = Geod(ellps='WGS84').inv(lon[startidx], lat[startidx], lon[endidx], lat[endidx])[2]
    with np.errstate(invalid='ignore', divide='ignore'):
        sinuosity = (distance[endidx] - distance[startidx]) / geodesic_distance

    speed = sinuosity_adjusted(track['speed_ms'], sinuosity)
    track.set(sinuosity=sinuosity, speed_ms=speed, timestamp=update_ride_time(track['timestamp'][0], track['delta_distance_m'], speed))
    return assets


def ride_time_s(parameters, track):
    """
    Riding time [s] for the parameters, reusing the elevation, gravity, weather and sinuosity of track
    (weather stays sampled at the original ride times).
    """
    speed = speed_without_wind(parameters, track)
    if track.has('headwind_ms'):
        speed = speed_with_wind(parameters, track, speed)
    speed = sinuosity_adjusted(speed, track['sinuosity'])
    return np.sum(track['delta_distance_m'][1:] / speed[1:])


SENSITIVITY_SWING = 0.01  # Relative change of each parameter for the sensitivities (±1 %)


class Sensitivity(NamedTuple):
    parameter: str          # e.g. 'System weight'
    step: str               # Absolute size of the 1 % change, e.g. '0.8 kg'
    minus_s: float          # Change of the riding time [s] with the parameter 1 % lower (< 0 = faster)
    plus_s: float           # Change of the riding time [s] with the parameter 1 % higher (< 0 = faster)


def sensitivities(parameters, track) -> list[Sensitivity]:
    """
    Change of the riding time if only one parameter is 1 % lower / higher, all others unchanged.
    Power and CdA are scaled for flat, uphill and downhill alike.
    """
    def ride_time_with(factor, name):
        changed = copy.copy(parameters)
        value = getattr(parameters, name)
        setattr(changed, name, PerGradient(*(v * factor for v in value)) if isinstance(value, PerGradient) else value * factor)
        return ride_time_s(changed, track)

    swing = SENSITIVITY_SWING
    base = ride_time_s(parameters, track)
    steps = {
        'weight': ('System weight', f'{parameters.weight * swing:.1f} kg'),
        'power': ('Power', f'{parameters.power.flat * swing:.1f} W flat'),
        'cda': ('CdA', f'{parameters.cda.flat * swing:.4f} m² flat'),
        'crr': ('Crr', f'{parameters.crr * swing:.5f}'),
    }
    return [Sensitivity(parameter, step, float(ride_time_with(1 - swing, name) - base), float(ride_time_with(1 + swing, name) - base))
            for name, (parameter, step) in steps.items()]


def calculate_compass_bearing(track):
    """
    Calculates the bearing between consecutive points.
    The formulae used is the following:
        θ = atan2(sin(Δlong).cos(lat2),
                  cos(lat1).sin(lat2) − sin(lat1).cos(lat2).cos(Δlong))
    :Returns:
      The bearing in degrees for every point (the first point gets the bearing of the first segment)
    """

    # Convert latitude and longitude from degrees to radians
    lat, lon = np.deg2rad(track['lat']), np.deg2rad(track['lon'])
    lat1, lat2, lon1, lon2 = lat[:-1], lat[1:], lon[:-1], lon[1:]

    # Calculate change in coordinates
    diff_long = lon2 - lon1

    # Calculate bearing
    x = np.sin(diff_long) * np.cos(lat2)
    y = np.cos(lat1) * np.sin(lat2) - (np.sin(lat1)
                                       * np.cos(lat2) * np.cos(diff_long))

    initial_bearing = np.arctan2(x, y)

    # Now we have the initial bearing but math.atan2 return values
    # from -180° to + 180° which is not what we want for a compass bearing
    # The solution is to normalize the initial bearing as shown below
    initial_bearing = np.rad2deg(initial_bearing)
    compass_bearing = (360 + initial_bearing) % 360

    return np.append(compass_bearing[0], compass_bearing)  # Because it's vectorized, length would be minus one


def windBearingProjection(track):
    ride_bearing = calculate_compass_bearing(track)
    return track['wind_speed_ms'] * np.cos(
    np.deg2rad(track['wind_direction_deg'])-np.deg2rad(ride_bearing)) # Because wind direction is the direction from where the wind is blowing

def calculate_yaw_angle(track):
    # Convert degrees to radians
    cycling_direction_rad = np.deg2rad(calculate_compass_bearing(track))
    wind_direction_rad = np.deg2rad(track['wind_direction_deg'])
    bicycle_speed = track['delta_distance_m']/np.append(0, np.diff(track['timestamp']))

    # Based on formula from https://www.andrewstarykowicz.com/what-is-yaw/
    # Calculate the difference in direction
    direction_diff = wind_direction_rad - cycling_direction_rad

    # Calculate yaw angle from arctan between cross wind and apparent wind speed (cycling velocity + projected wind speed)
    yaw_angle_rad = np.arctan((track['wind_speed_ms']*np.sin(direction_diff))/(bicycle_speed+track['headwind_ms']))

    # Convert yaw angle from radians to degrees
    return np.rad2deg(yaw_angle_rad)

def moving_average(a, n=30) :
    ret = np.cumsum(a, dtype=float)
    ret[n:] = ret[n:] - ret[:-n]
    return ret[n - 1:] / n

def calculate_power_numbers(timestamp, gradient, power, threshold):
    time_array = np.arange(timestamp[0], timestamp[-1]) # Used to calculate NP
    power_array = np.zeros(time_array.shape, dtype=float) # Storage for power at seconds level
    powerSeq_downhill = np.split(np.where(gradient <= -threshold)[0], np.where(np.diff(np.where(gradient <= -threshold)[0]) != 1)[0]+1)
    powerSeq_uphill = np.split(np.where(gradient >= threshold)[0], np.where(np.diff(np.where(gradient >= threshold)[0]) != 1)[0]+1)
    for sequence in powerSeq_uphill:
        if len(sequence)>0:
            power_array[(np.abs(time_array - timestamp[np.nanmin(sequence)])).argmin():(np.abs(time_array - timestamp[np.nanmax(sequence)])).argmin()] = power.uphill
    for sequence in powerSeq_downhill:
        if len(sequence)>0:
            power_array[(np.abs(time_array - timestamp[np.nanmin(sequence)])).argmin():(np.abs(time_array - timestamp[np.nanmax(sequence)])).argmin()] = power.downhill
    power_array[np.where(power_array==0)[0]] = power.flat
    power_avg = np.mean(power_array)
    power_np = np.mean(moving_average(power_array,30)**4)**.25
    return PowerSummary(average_w=power_avg, normalized_w=power_np)
