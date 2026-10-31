import copy
import warnings
from typing import NamedTuple

import numpy as np
from scipy import signal

import config
import utilities
import weather
from parameters import PerGradient, PowerSummary

RHO0 = 1.225  # Air density at sea level and 15 °C [kg/m³]
MAX_SPEED_MS = config.MAX_SPEED_KMH / 3.6  # Top speed (config max_speed_kmh)
DRIVETRAIN_EFFICIENCY = 0.98

# Cornering: rider level -> (accepted lateral acceleration, braking deceleration) [g]
CORNERING = {'cautious': (0.30, 0.30), 'average': (0.45, 0.40), 'confident': (0.60, 0.50), 'racer': (0.80, 0.60)}
GRIP_DRY_G = 0.9              # Tyre grip on dry asphalt: upper limit of the lateral acceleration [g]
PEDAL_STRIKE_LEAN_DEG = 30    # No pedalling at larger lean angles (inside pedal down would touch the road)
MAX_ACCELERATION_G = 0.3      # Acceleration out of corners at most (traction, maximum torque) [g]
WHEEL_INERTIA = 0.14          # Moment of inertia of both wheels [kg·m²] (Martin et al. 1998)
WHEEL_RADIUS = 0.311          # Outer tyre radius [m] (Martin et al. 1998)
WET_ROAD_MM_PER_H = 0.2       # Road counted as wet from this precipitation in the hour before the rider passes
WET_GRIP_G = 0.6              # Tyre grip on wet asphalt [g]
WET_CAUTION = 0.75            # Riders accept this share of their lateral acceleration and braking on wet roads
CURVE_HALF_SPAN_M = 15        # Curve radius from the route positions this far before and after each point
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

    track.set(delta_distance_m=delta_distance, delta_elevation_m=delta_elevation, gradient=gradient,
              curve_radius_m=curve_radius(track['lat'], track['lon'], track['distance_m']))

    assets = noWeatherAdjustment(assets, parameters)
    if parameters.time: # If no time is given -> no weather adjustment
        if weather.add_weather(assets, parameters):
            assets = weatherAdjustment(assets, parameters)

    return assets


def curve_radius(lat, lon, distance, half_span=CURVE_HALF_SPAN_M):
    """
    Radius [m] of the circle through the route positions half_span metres before, at and after each point
    (interpolated along the route, so independent of the point spacing); inf on straight stretches.
    """
    before, after = np.clip(distance - half_span, distance[0], distance[-1]), np.clip(distance + half_span, distance[0], distance[-1])
    scale = np.cos(np.deg2rad(lat)) * 111320  # Metres per degree longitude at each point (latitude: 111320)
    ax, ay = (np.interp(before, distance, lon) - lon) * scale, (np.interp(before, distance, lat) - lat) * 111320
    cx, cy = (np.interp(after, distance, lon) - lon) * scale, (np.interp(after, distance, lat) - lat) * 111320
    a, b, c = np.hypot(cx, cy), np.hypot(ax - cx, ay - cy), np.hypot(ax, ay)  # Sides of the triangle (point at the origin)
    with np.errstate(divide='ignore', invalid='ignore'):
        radius = a * b * c / (2 * np.abs(ax * cy - cx * ay))
    return np.where(np.isfinite(radius), radius, np.inf)


def per_gradient(gradient, setting, threshold):
    """Value of a PerGradient setting for every point (uphill: gradient >= threshold, downhill: <= -threshold)."""
    values = np.full(len(gradient), setting.flat, dtype=float)
    values[gradient >= threshold] = setting.uphill
    values[gradient <= -threshold] = setting.downhill
    return values


def air_density_from_temperature(temperature, gravity, elevation):
    # RHO0 at 15 °C (288.15 K); the exponent is the isothermal barometric formula with scale height 101325 / (RHO0 * g) ≈ 8.4 km
    return RHO0 * (288.15 / (temperature + 273.15) * np.exp(-RHO0 * gravity * (elevation / 101325)))


def air_density_from_weather(temperature, relative_humidity, surface_pressure):
    # True air density based on relative humidity, temperature and surface pressure
    R_d = 287.058 # specific gas constant for dry air [J/(kg·K)]
    R_v = 461.495 # specific gas constant for water vapor [J/(kg·K)]
    temperature = temperature + 273.15 # Temperature in Kelvin
    surface_pressure = surface_pressure * 100 # Surface pressure in Pa
    p_sat = 610.78*np.exp((17.27*(temperature-273.15))/(temperature-35.85)) # saturation vapor pressure [Pa] (Tetens)
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


def resistance_terms(parameters, track, with_weather):
    """Per point: air drag factor 1/2*rho*CdA [kg/m], other resistances (rolling, gravity) [N] and head wind [m/s]."""
    gradient = track['gradient']
    cda = per_gradient(gradient, parameters.cda, parameters.gradient_threshold)
    weight_force = parameters.weight * track['gravity_ms2']
    if with_weather:
        air_density, headwind = track['air_density_kgm3'], track['headwind_ms']
    else:  # Standard atmosphere at 15 °C, no wind
        air_density = air_density_from_temperature(15, track['gravity_ms2'], np.nan_to_num(track['elevation_m']))
        headwind = np.zeros(len(track))
    angle = np.arctan(gradient)
    return air_density / 2 * cda, parameters.crr * weight_force * np.cos(angle) + weight_force * np.sin(angle), headwind


def steady_speed(drag, resistance, headwind, power):
    """
    Speed [m/s] at which the power at the wheel [W] balances the resistances; NaN without a solution.
    Air force 1/2*rho*CdA*(v+w)*|v+w|: from the front if v + w >= 0, from behind (tail wind faster than the rider)
    otherwise; the resistance grows strictly with v, so there is exactly one valid root.
    """
    front = cubic_roots_real_parts(drag, 2 * headwind * drag, headwind ** 2 * drag + resistance, -power)
    behind = cubic_roots_real_parts(-drag, -2 * headwind * drag, -headwind ** 2 * drag + resistance, -power)
    valid = np.concatenate([(front > 0) & (front + headwind >= 0), (behind > 0) & (behind + headwind < 0)])
    speed = np.max(np.where(valid, np.concatenate([front, behind]), -np.inf), axis=0)
    return np.where(np.isfinite(speed), speed, np.nan)


def steady_speeds(parameters, track, with_weather):
    """
    Steady-state speed [m/s] at every point with the rider's power for the gradient, without (with_weather=False: 15 °C, no wind)
    or with the weather columns of track. Above config.MAX_PEDALLING_SPEED_KMH the rider stops pedalling (cadence
    limit): faster only where gravity or wind alone make it, otherwise held at that speed.
    """
    drag, resistance, headwind = resistance_terms(parameters, track, with_weather)
    power = per_gradient(track['gradient'], parameters.power, parameters.gradient_threshold) * DRIVETRAIN_EFFICIENCY
    speed = forward_fill(steady_speed(drag, resistance, headwind, power))  # Keep previous speed if there is no solution
    pedalling_limit = config.MAX_PEDALLING_SPEED_KMH / 3.6
    if (speed > pedalling_limit).any():
        coasting = np.nan_to_num(steady_speed(drag, resistance, headwind, np.full(len(speed), 1e-3)))
        speed = np.where(speed > pedalling_limit, np.maximum(coasting, pedalling_limit), speed)
    return np.minimum(speed, MAX_SPEED_MS)


def cornered_speeds(parameters, track, steady, with_weather):
    """
    Speed [m/s] at every point with the corners: limit sqrt(a_lat * r) per point (rider level, at most the tyre grip),
    braking before corners (backward pass) and accelerating after them with the rider's power (forward pass, which also
    carries momentum into short climbs). No pedalling above the pedal strike lean angle or the cadence limit.
    With weather, roads are wet where the precipitation reaches WET_ROAD_MM_PER_H: less grip and more caution.
    Braking and cornering share the tyre grip (friction circle): where the bike corners hard, less braking is left.
    """
    level_lateral_g, level_braking_g = CORNERING[parameters.cornering]
    gravity = track['gravity_ms2']
    radius, step = track['curve_radius_m'], track['delta_distance_m']
    lateral_g = np.full(len(radius), min(level_lateral_g, GRIP_DRY_G))
    braking_g = np.full(len(radius), level_braking_g)
    grip_g = np.full(len(radius), GRIP_DRY_G)
    if with_weather and track.has('precipitation_mm_s'):
        wet = np.nan_to_num(track['precipitation_mm_s']) * 3600 >= WET_ROAD_MM_PER_H
        lateral_g[wet] = min(level_lateral_g * WET_CAUTION, WET_GRIP_G)
        braking_g[wet] = min(level_braking_g * WET_CAUTION, WET_GRIP_G)
        grip_g[wet] = WET_GRIP_G
    limit = np.minimum(np.sqrt(lateral_g * gravity * radius), MAX_SPEED_MS).tolist()
    braking, grip, radius_list = (braking_g * gravity).tolist(), (grip_g * gravity).tolist(), radius.tolist()
    for i in range(len(limit) - 2, -1, -1):  # Braking: arrive at most this fast to make the next limit
        lateral = limit[i + 1] ** 2 / radius_list[i + 1]  # Lateral acceleration at the next point [m/s²]
        available = min(braking[i + 1], max(grip[i + 1] ** 2 - lateral ** 2, 0.0) ** 0.5)  # Friction circle
        limit[i] = min(limit[i], (limit[i + 1] ** 2 + 2 * available * step[i + 1]) ** 0.5)

    drag, resistance, headwind = resistance_terms(parameters, track, with_weather)
    power = per_gradient(track['gradient'], parameters.power, parameters.gradient_threshold) * DRIVETRAIN_EFFICIENCY
    mass, pedalling_limit = parameters.weight, config.MAX_PEDALLING_SPEED_KMH / 3.6
    inertial_mass = mass + WHEEL_INERTIA / WHEEL_RADIUS ** 2  # Kinetic energy term incl. the rotating wheels (Martin et al. Eq. 12)
    pedal_strike = np.tan(np.deg2rad(PEDAL_STRIKE_LEAN_DEG)) * gravity * radius  # v**2 above which the pedal would touch
    max_acceleration = MAX_ACCELERATION_G * gravity
    drag, resistance, headwind, power, pedal_strike, max_acceleration, steady = (
        x.tolist() for x in (drag, resistance, headwind, power, pedal_strike, max_acceleration, np.asarray(steady)))
    speed = [min(steady[0], limit[0])]
    for i in range(1, len(limit)):
        v = speed[-1]
        pedalling = v < pedalling_limit and v * v <= pedal_strike[i]
        air = v + headwind[i]
        force = (power[i] / max(v, 1.0) if pedalling else 0.0) - drag[i] * air * abs(air) - resistance[i]
        acceleration = min(force / inertial_mass, max_acceleration[i])
        reached = max(v * v + 2 * acceleration * step[i], 0.0) ** 0.5
        # Towards the steady speed: accelerating up to it, decelerating (momentum on climbs) down to it
        reached = min(reached, steady[i]) if acceleration >= 0 else max(reached, steady[i])
        speed.append(min(reached, limit[i]))
    return np.array(speed)


def noWeatherAdjustment(assets, parameters):
    print('- Calculating Rough Speeds (w/o Weather Data)')
    track = assets.track
    speed = cornered_speeds(parameters, track, steady_speeds(parameters, track, with_weather=False), with_weather=False)

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

    speed = cornered_speeds(parameters, track, steady_speeds(parameters, track, with_weather=True), with_weather=True)

    timezone_offset = utilities.get_timezone_offset(assets, parameters)
    track.set(speed_ms=speed, timestamp=update_ride_time(parameters.time - timezone_offset, track['delta_distance_m'], speed))
    setattr(parameters, 'power_out', calculate_power_numbers(track['timestamp'], track['gradient'], parameters.power, parameters.gradient_threshold))
    track.set(yaw_angle_deg=calculate_yaw_angle(track))

    return assets


def ride_time_s(parameters, track):
    """
    Riding time [s] for the parameters, reusing the elevation, gravity, curve radii and weather of track
    (weather stays sampled at the original ride times).
    """
    with_weather = track.has('headwind_ms')
    speed = cornered_speeds(parameters, track, steady_speeds(parameters, track, with_weather), with_weather)
    return np.sum(track['delta_distance_m'][1:] / speed[1:])


class Sensitivity(NamedTuple):
    parameter: str          # e.g. 'System weight'
    step: str               # Absolute size of the change, e.g. '0.8 kg'
    minus_s: float          # Change of the riding time [s] with the parameter lower (< 0 = faster)
    plus_s: float           # Change of the riding time [s] with the parameter higher (< 0 = faster)


def sensitivities(parameters, track) -> list[Sensitivity]:
    """
    Change of the riding time if only one parameter is parameters.whatif_pct lower / higher, all others unchanged.
    Power and CdA are scaled for flat, uphill and downhill alike.
    """
    def ride_time_with(factor, name):
        changed = copy.copy(parameters)
        value = getattr(parameters, name)
        setattr(changed, name, PerGradient(*(v * factor for v in value)) if isinstance(value, PerGradient) else value * factor)
        return ride_time_s(changed, track)

    swing = parameters.whatif_pct / 100
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
    bicycle_speed = track['speed_ms']

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
