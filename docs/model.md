# How AerOracle calculates

This page describes how AerOracle gets from a route and a few rider values to a riding time: the order of the
steps, the physics, the values it uses and where its limits are. The values given are the defaults; most can be
changed in the web app, on the command line or in `config.toml`.

## Overview

1. **Route**: the points of a GPX, TCX or FIT file, or of a RideWithGPS route or trip.
2. **Elevation**: the elevations in the file are replaced by those of an elevation model. In tunnels and on
   bridges the values are interpolated, before a smoothing is applied on the profile.
3. **Gravity**: calculated for every point, based on latitude, elevation and a gravity anomaly map.
4. **First pass**: models speeds in still air at 15 °C, giving time estimates when the rider passes every point along the route.
5. **Weather**: based on the times from the first pass, wind, temperature, humidity, pressure, and precipitation forecasts are retrieved.
6. **Second pass**: models speeds with the influence of the weather for every point along the route.
7. **Extras**: climbs, terrain shadow, the decomposition of the rider's power, course files for the head unit.

## Forces and speed

At every point, the rider's power minus the drivetrain losses must equal the resistance from needed air, rolling
resistance and gravity, plus the power that goes into accelerating (Martin et al. 1998):

$$
\eta P = v \left[ \tfrac{1}{2} \rho C_dA (v + w) |v + w| + m g \left( C_{rr} \cos\theta + \sin\theta \right) \right] + m_\text{eff} a v
$$

| Symbol | Meaning | Value |
|---|---|---|
| $P$ | rider power | per section: flat, uphill, downhill (default 205 / 225 / 0 W) |
| $\eta$ | drivetrain efficiency | 0.98 |
| $v$ | speed | |
| $w$ | head wind at rider height (negative: tail wind) | see *Weather* |
| $\rho$ | air density | see *Weather* |
| $C_dA$ | drag area | per section (default 0.35 / 0.40 / 0.29 m²) |
| $m$ | system weight: rider, bike, accessories | default 82 kg |
| $g$ | gravity at the point | see below |
| $C_{rr}$ | rolling resistance coefficient | default 0.0035 (or Prr, converted at 8 m/s) |
| $\theta$ | slope angle, $\tan\theta$ = gradient | |
| $m_\text{eff}$ | mass including the rotating wheels: $m + I/r^2$ | $I$ = 0.14 kg·m², $r$ = 0.311 m (+1.45 kg) |
| $a$ | acceleration | |

**Sections**: a point counts as uphill from a gradient of +5% and as downhill from −5% (`--gradient`); power and
CdA change there. Weight and Crr are the same everywhere.

**Steady speed**: the speed at which power and resistances balance ($a = 0$), solved directly for $v$: the speed the
rider holds as long as the external conditions (gradient, wind, corners) stay the same. Accelerating and braking are
described under *Corners and momentum*.

**Speed limits**:
- Above **65 km/h** the rider stops pedalling (cadence limit): faster only where gravity or wind alone make it.
- Top speed **80 km/h** (`max_speed_kmh`), e.g. on long straight descents.

**Gravity**: normal gravity by latitude and elevation (WELMEC formula), plus the free-air anomaly of the World Gravity
Map (WGM2012). Its effect on the riding time is minimal. Added mainly to show off ;)

## Corners and momentum

**Curve radius**: at every point, the circle through the route positions 15 m before, at and 15 m after it.

**Corner speed**: $v_\text{max} = \sqrt{a_\text{lat} g r}$, with the lateral acceleration the rider accepts:

| Cornering style | Lateral acceleration | Braking | Lean angle | Speed in a tight hairpin (r = 10 m) |
|---|---|---|---|---|
| Cautious | 0.30 g | 0.30 g | 17° | ~20 km/h |
| Average (default) | 0.45 g | 0.40 g | 24° | ~24 km/h |
| Confident | 0.60 g | 0.50 g | 31° | ~28 km/h |
| Racer | 0.80 g | 0.60 g | 39° | ~32 km/h |

At most the tyre grip on dry asphalt, 0.9 g.

**Braking**: going backwards along the route, the speed before every corner is limited so that the rider reaches
the corner speed at the corner entry. The braking deceleration is that of the cornering style, but at most the tyre grip
left over from cornering (friction circle): where the bike already corners hard, less braking is possible.

**Accelerating**: going forwards, the rider accelerates out of corners with the power set for the gradient there
(flat, uphill or downhill). The acceleration is what remains of the rider's force after air, rolling resistance and
gravity, at most 0.3 g:

$$
a = \frac{\eta P / v - \tfrac{1}{2} \rho C_dA (v + w) |v + w| - m g \left( C_{rr} \cos\theta + \sin\theta \right)}{m_\text{eff}}
$$

Speed from a descent carries into a short climb until it has dropped to the steady speed.

**No pedalling**: above a lean angle of 30° (the inside pedal would touch the road), above the cadence limit and
while braking for a corner.

**Wet roads**: where the forecast has at least 0.2 mm of precipitation in the hour before the rider passes, the
maximum tyre grip drops from 0.9 to 0.6 g, and the rider accepts only 75% of the cornering and braking of the chosen
style (at most the 0.6 g).

## Weather

**Forecast points**: one forecast for every 10 minutes of riding time, at the modeled place and time the rider passes; on long rides
spaced further apart (in 5-minute steps), so that there are at most 50 forecasts.

**Providers**: Open-Meteo by default, Visual Crossing if it fails; meteoblue optionally. By date:

| Start | Source (Open-Meteo) |
|---|---|
| before 2024 | ERA5 reanalysis (archive) |
| 2024 to two days ago | historical forecasts |
| from two days ago, up to 7 days ahead | forecast; within a day of now with 15-minute values |

**Along the route**: the values are interpolated from the forecast points to every route point: linearly, wind speed
and direction quadratically (direction without jumps at north).

**Wind at rider height**: the forecast wind is given 10 m above ground. AerOracle scales it to the rider's torso
(1.2 m) with a power law, $w_{1.2} = w_{10} (1.2/10)^{0.5}$, i.e. to about 35%. The power law with a
terrain-dependent exponent is the standard description of the wind near the ground
([wind gradient](https://en.wikipedia.org/wiki/Wind_gradient)),
with exponents of about 0.1 over open water, 0.15 in open country and up to 0.4 in towns, for heights above about
10 m. Down at rider height, hedges, trees and buildings slow the wind further, so AerOracle uses an effective
exponent of 0.5 (`wind_shear_exponent`): its own fit to measured rides (best fits 0.6 to 0.8; 0.5 as a compromise
for more open terrain).

**Head wind**: the component of the wind along the direction of travel,
$w = w_{1.2} \cos(\text{wind direction} - \text{bearing})$. Cross wind is shown on the map but has no effect on the
speed: the drag area does not depend on the wind angle (yaw) in the model.

**Air density**: from temperature, relative humidity and surface pressure (moist air), where the provider has them;
otherwise from temperature and elevation (barometric formula). Without weather: 15 °C.

## Elevation

**Elevation models**: the first that covers a point is used.

| Model | Area | Resolution |
|---|---|---|
| swissALTI3D (swisstopo) | Switzerland | 5 m |
| Sonny's LiDAR DTMs | parts of Europe | 1″ (about 30 m) |
| Copernicus DEM GLO-30 | worldwide | 1″ |
| ALOS AW3D30 | worldwide | 1″ |
| EU-DEM | Europe | 25 m |
| ASTER GDEM | worldwide | 1″ |
| SRTM | 60° S to 60° N | 3″ (about 90 m), 1″ in the USA |

Copernicus, ALOS and ASTER are surface models: in forests and towns they include trees and buildings.

**Tunnels and bridges**: from OpenStreetMap. An elevation model shows the mountain above a tunnel and the valley below a
bridge. Route points within 30 m of a tunnel or bridge on a road or cycleway are therefore interpolated linearly
between the points before and after. Bridges count from 25 m length; tunnels of any length, because even a short
underpass shows up as the embankment above it.

**Climbs**: a climb starts where the next 200 m rise by at least 4%, ends after a descent of more than 20 m, and
counts from 500 m length and 50 m of ascent. Their summits are written to the course files, if chosen.

## Terrain shadow (optional)

Every 100 m along the route: the sun's position at the time the rider passes, and the horizon in the sun's direction
(rays out to 60 km over the local elevation models, with Earth curvature and refraction). Sun, shade, tunnel or
night per point; stretches of shade or sun shorter than 300 m are dropped. Terrain only: trees, buildings and clouds
are not included.

## Power decomposition

The *Power decomposition* plot shows where the rider's power goes, in sections of at least 50 m: air (still air and
the wind's share), rolling resistance, climbing, accelerating and the drivetrain losses. On descents and with a tail wind,
gravity and wind do part of the work; the plot leaves that part out.

## Accuracy and limits

**Checked against measured rides**: 155 rides of the author (power meter, mainly Switzerland). With the default
values, the predicted riding time deviates from the measured one by a median of 0.7%, with a spread of 2.5%; from
−3% gradient upwards it stays within ±2.5%. On steep descents the measured rides were slower (see below). The cornering style *confident* matched the measured corners (0.58 g).

**Not modelled**:
- **Stops and traffic**: junctions, traffic lights, roundabouts, towns. Riding times are moving times.
- **Speed on straight descents**: the model rides as fast as physics and the top speed allow; real riders often brake
  for narrow or bumpy roads, traffic or comfort.
- **Drafting, pacing, fatigue**: no riding in a group; the power is constant per section and does not drop over time.
- **Road surface**: one Crr for the whole route; gravel or rough asphalt need a higher value.
- **Cross wind**: it has no effect on the speed, and the drag area does not depend on the wind angle.
- **Weather forecast**: the result is only as good as the forecast, especially the wind.

## References

- Martin, J. C., Milliken, D. L., Cobb, J. E., McFadden, K. L., & Coggan, A. R. (1998). Validation of a Mathematical
  Model for Road Cycling Power. *Journal of Applied Biomechanics*, 14(3), 276–291.
  [doi:10.1123/jab.14.3.276](https://doi.org/10.1123/jab.14.3.276)
- Weather: [Open-Meteo](https://open-meteo.com/), [Visual Crossing](https://www.visualcrossing.com/),
  [meteoblue](https://www.meteoblue.com/). Elevation and OpenStreetMap: see the sources in the [README](../README.md).
