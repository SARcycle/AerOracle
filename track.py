"""
GPS Track Class
Holds the route as two polars DataFrames:
- track.points: one row per track point (see POINT_COLUMNS)
- track.cues:   one row per course point / cue (see CUE_COLUMNS)
Supports GPX, TCX, FIT and RideWithGPS JSON files.
"""

from datetime import datetime as dt
from pathlib import Path
import json
import xml.etree.ElementTree as ET

import numpy as np
import polars as pl


# Columns of track.points. Missing float values are NaN (not -999).
# Columns are added along the pipeline; the module that adds them is noted.
POINT_COLUMNS = {
    'lat': pl.Float64,                # Latitude [°, WGS84]                          (track)
    'lon': pl.Float64,                # Longitude [°, WGS84]                         (track)
    'timestamp': pl.Float64,          # Unix time [s, UTC]; estimated ride time      (track, speedcalculation)
    'distance_m': pl.Float64,         # Cumulative distance [m]                      (track)
    'elevation_m': pl.Float64,        # Elevation [m a.s.l.]                         (dem)
    # 'dem_source': DEM_SOURCE        # DEM the elevation was taken from             (dem)
    # 'gravity_ms2'                   # Local gravitational acceleration [m/s²]      (assets)
    # 'delta_distance_m'              # Distance to previous point [m]               (speedcalculation)
    # 'delta_elevation_m'             # Elevation change to previous point [m]       (speedcalculation)
    # 'gradient'                      # Smoothed gradient [-] (0.05 = 5%)            (speedcalculation)
    # 'speed_ms'                      # Estimated speed [m/s]                        (speedcalculation)
    # 'temperature_c'                 # Air temperature [°C]                         (weather)
    # 'wind_direction_deg'            # Wind direction (where it blows from) [°]     (weather)
    # 'wind_speed_ms'                 # Wind speed at rider height [m/s]             (weather)
    # 'is_forecast_point'             # True where a forecast was requested          (weather)
    # 'relative_humidity'             # Relative humidity [0.0-1.0]                  (weather, Open-Meteo only)
    # 'surface_pressure_hpa'          # Surface pressure [hPa]                       (weather, Open-Meteo only)
    # 'air_density_kgm3'              # Air density [kg/m³]                          (speedcalculation)
    # 'headwind_ms'                   # Wind projected on riding direction [m/s], >0 = head wind (speedcalculation)
    # 'precipitation_mm_s'            # Precipitation rate [mm/s]                    (weather)
    # 'curve_radius_m'                # Curve radius of the route [m], inf = straight (speedcalculation)
    # 'yaw_angle_deg'                 # Apparent wind yaw angle [°], >0 = from right (speedcalculation)
}

# Columns of track.cues
CUE_COLUMNS = {
    'lat': pl.Float64,                # Latitude [°, WGS84]
    'lon': pl.Float64,                # Longitude [°, WGS84]
    'timestamp': pl.Float64,          # Unix time [s, UTC], NaN if unknown
    'distance_m': pl.Float64,         # Distance along the track [m]
    'name': pl.String,                # Short name shown on the device
    'type': pl.String,                # TCX PointType (e.g. 'Left', 'Summit', 'Generic')
    'notes': pl.String,               # Free text
}


def parse_timestamp(text) -> float:
    """ISO 8601 time string -> Unix timestamp [s]; NaN if missing or unparsable."""
    if not text:
        return np.nan
    try:
        return dt.fromisoformat(text.strip()).timestamp()
    except ValueError:
        return np.nan


def calculate_cumulative_distance_np(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Calculate cumulative distance using vectorized haversine formula."""
    if len(lats) == 0:
        return np.array([])

    R = 6371000  # Earth radius in meters

    # Convert to radians
    lat1 = np.radians(lats[:-1])
    lat2 = np.radians(lats[1:])
    lon1 = np.radians(lons[:-1])
    lon2 = np.radians(lons[1:])

    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat/2)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2)**2
    c = 2 * np.arcsin(np.sqrt(a))
    distances = R * c

    cumulative = np.concatenate([[0], np.cumsum(distances)])
    return cumulative


class Track:
    """
    GPS Track parser.

    Attributes:
    - track.points: pl.DataFrame, one row per track point (POINT_COLUMNS)
    - track.cues:   pl.DataFrame, one row per course point (CUE_COLUMNS)

    Usage:
        track = Track(parameters)
        print(f"Points: {len(track)}")
        print(f"Distance: {track['distance_m'][-1]/1000:.2f} km")
    """

    def __init__(self, parameters):
        rwgps_json = getattr(parameters, 'rwgps_json', None)
        if rwgps_json:
            self.filepath = Path(rwgps_json)
            self.format = 'rwgps'
        else:
            self.filepath = Path(parameters.input)
            self.format = self._detect_format()

        self.points = pl.DataFrame(schema=POINT_COLUMNS)
        self.cues = pl.DataFrame(schema=CUE_COLUMNS)

        # Parse the file
        self._parse()

    def __len__(self) -> int:
        return self.points.height

    def __getitem__(self, column: str) -> np.ndarray:
        """Read-only numpy view of a point column, e.g. track['elevation_m']."""
        return self.points[column].to_numpy()

    def has(self, column: str) -> bool:
        return column in self.points.columns

    def set(self, **columns) -> None:
        """Add or replace point columns from arrays of length len(track)."""
        self.points = self.points.with_columns(
            pl.Series(name, np.asarray(values)) for name, values in columns.items())

    def add_cue(self, point_idx: int, name: str, type: str, notes: str) -> None:
        """Append a cue located at track point point_idx."""
        point = self.points.row(point_idx, named=True)
        self.cues = pl.concat([self.cues, pl.DataFrame(
            [(point['lat'], point['lon'], point['timestamp'], point['distance_m'], name, type, notes)],
            schema=CUE_COLUMNS, orient='row')])

    def _detect_format(self) -> str:
        """Detect file format."""
        ext = self.filepath.suffix.lower()
        if ext in ['.gpx']:
            return 'gpx'
        elif ext in ['.tcx']:
            return 'tcx'
        elif ext in ['.fit']:
            return 'fit'
        elif ext in ['.json']:
            return 'rwgps'

        # Fallback: check content
        with open(self.filepath, 'rb') as f:
            header = f.read(14)
            if header[:4] == b'.FIT':
                return 'fit'

        with open(self.filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read(500)
            if '<gpx' in content:
                return 'gpx'
            elif 'TrainingCenterDatabase' in content:
                return 'tcx'

        raise ValueError(f"Unknown GPS file format: {self.filepath}")

    def _parse(self):
        """Parse the GPS file and set attributes."""
        print(f'- Parsing {self.format.upper()} file')
        if self.format == 'gpx':
            self._parse_gpx()
        elif self.format == 'tcx':
            self._parse_tcx()
        elif self.format == 'fit':
            self._parse_fit()
        elif self.format == 'rwgps':
            self._parse_rwgps_json()

    def _set_parsed(self, track_points: list, cue_points: list):
        """
        Store parsed rows.
        track_points: [(lat, lon, timestamp, distance_m, elevation_m), ...]
        cue_points:   [(lat, lon, timestamp, distance_m, name, type, notes), ...]
        """
        self.points = pl.DataFrame(track_points, schema=POINT_COLUMNS, orient='row')
        self.cues = pl.DataFrame(cue_points, schema=CUE_COLUMNS, orient='row')

        # Calculate distance if not present
        if len(self) > 0 and (self['distance_m'] == 0).all():
            self.set(distance_m=calculate_cumulative_distance_np(self['lat'], self['lon']))
        self._assign_crs_distances()

    def _parse_gpx(self):
        """Parse GPX file."""
        track_points = []
        cue_points = []

        try:
            tree = ET.parse(self.filepath)
            root = tree.getroot()

            # Extract track points
            for trkpt in root.findall('.//{*}trkpt'):
                lat = float(trkpt.get('lat'))
                lon = float(trkpt.get('lon'))

                ele_elem = trkpt.find('{*}ele')
                ele = float(ele_elem.text) if ele_elem is not None else np.nan

                timestamp = parse_timestamp(trkpt.findtext('{*}time'))

                track_points.append((lat, lon, timestamp, 0, ele))

            # Check route points <rte><rtept>
            route_points = root.findall('.//{*}rtept')

            if route_points and not track_points:
                # No track data from <trkpt>, use route points as track
                turn_keywords = ['right', 'left', 'straight', 'continue', 'roundabout',
                                'exit', 'end', 'start', 'sharp', 'slight', 'keep']

                for rtept in route_points:
                    lat = float(rtept.get('lat'))
                    lon = float(rtept.get('lon'))

                    ele_elem = rtept.find('{*}ele')
                    ele = float(ele_elem.text) if ele_elem is not None else np.nan

                    timestamp = parse_timestamp(rtept.findtext('{*}time'))

                    # Always add to track
                    track_points.append((lat, lon, timestamp, 0, ele))

                    # Also extract as course point if it has turn directions
                    name_elem = rtept.find('{*}name')
                    if name_elem is not None and name_elem.text:
                        name = name_elem.text

                        if any(keyword in name.lower() for keyword in turn_keywords):
                            cmt_elem = rtept.find('{*}cmt')
                            desc_elem = rtept.find('{*}desc')
                            notes = cmt_elem.text if cmt_elem is not None else (desc_elem.text if desc_elem is not None else '')

                            cue_points.append((lat, lon, timestamp, 0, name, name, notes))

            # Extract waypoints
            for wpt in root.findall('.//{*}wpt'):
                lat = float(wpt.get('lat'))
                lon = float(wpt.get('lon'))

                name_elem = wpt.find('{*}name')
                name = name_elem.text if name_elem is not None else ''

                desc_elem = wpt.find('{*}desc')
                cmt_elem = wpt.find('{*}cmt')
                notes = desc_elem.text if desc_elem is not None else (cmt_elem.text if cmt_elem is not None else '')

                type_elem = wpt.find('{*}type')
                cue_type = type_elem.text if type_elem is not None else name

                cue_points.append((lat, lon, np.nan, 0, name, cue_type, notes))

        except Exception as e:
            raise RuntimeError(f"Error parsing GPX file: {e}")

        # Validate that we have track data
        if not track_points:
            raise ValueError(f"No track points found in {self.filepath}. "
                           f"The file may contain only waypoints or be malformed. "
                           f"GPX files need <trkpt> or <rtept> elements.")

        self._set_parsed(track_points, cue_points)


    def _parse_tcx(self):
        """Parse TCX file."""
        track_points = []
        cue_points = []

        try:
            tree = ET.parse(self.filepath)
            root = tree.getroot()

            is_course = root.find('.//{*}Courses') is not None

            if is_course:
                # Parse Course file
                for trackpoint in root.findall('.//{*}Track/{*}Trackpoint'):
                    position = trackpoint.find('{*}Position')
                    if position is not None:
                        lat_elem = position.find('{*}LatitudeDegrees')
                        lon_elem = position.find('{*}LongitudeDegrees')

                        if lat_elem is not None and lon_elem is not None:
                            lat = float(lat_elem.text)
                            lon = float(lon_elem.text)

                            ele_elem = trackpoint.find('{*}AltitudeMeters')
                            ele = float(ele_elem.text) if ele_elem is not None else np.nan

                            dist_elem = trackpoint.find('{*}DistanceMeters')
                            dist = float(dist_elem.text) if dist_elem is not None else 0

                            timestamp = parse_timestamp(trackpoint.findtext('{*}Time'))

                            track_points.append((lat, lon, timestamp, dist, ele))

                # Parse course points
                for cp in root.findall('.//{*}CoursePoint'):
                    position = cp.find('{*}Position')
                    if position is not None:
                        lat_elem = position.find('{*}LatitudeDegrees')
                        lon_elem = position.find('{*}LongitudeDegrees')

                        if lat_elem is not None and lon_elem is not None:
                            lat = float(lat_elem.text)
                            lon = float(lon_elem.text)

                            name_elem = cp.find('{*}Name')
                            name = name_elem.text if name_elem is not None else ''

                            notes_elem = cp.find('{*}Notes')
                            notes = notes_elem.text if notes_elem is not None else ''

                            type_elem = cp.find('{*}PointType')
                            cue_type = type_elem.text if type_elem is not None else ''

                            dist_elem = cp.find('{*}DistanceMeters')
                            dist = float(dist_elem.text) if dist_elem is not None else 0

                            timestamp = parse_timestamp(cp.findtext('{*}Time'))

                            cue_points.append((lat, lon, timestamp, dist, name, cue_type, notes))
            else:
                # Parse Activity file
                for lap in root.findall('.//{*}Lap'):
                    for trackpoint in lap.findall('.//{*}Trackpoint'):
                        position = trackpoint.find('{*}Position')
                        if position is not None:
                            lat_elem = position.find('{*}LatitudeDegrees')
                            lon_elem = position.find('{*}LongitudeDegrees')

                            if lat_elem is not None and lon_elem is not None:
                                try:
                                    lat = float(lat_elem.text)
                                    lon = float(lon_elem.text)

                                    ele_elem = trackpoint.find('{*}AltitudeMeters')
                                    ele = float(ele_elem.text) if ele_elem is not None else np.nan

                                    dist_elem = trackpoint.find('{*}DistanceMeters')
                                    dist = float(dist_elem.text) if dist_elem is not None else 0

                                    timestamp = parse_timestamp(trackpoint.findtext('{*}Time'))

                                    track_points.append((lat, lon, timestamp, dist, ele))
                                except (ValueError, TypeError):
                                    continue

        except Exception as e:
            raise RuntimeError(f"Error parsing TCX file: {e}")

        self._set_parsed(track_points, cue_points)

    def _parse_fit(self):
        """Parse FIT file."""
        try:
            from fitparse import FitFile
        except ImportError:
            raise ImportError("fitparse required for FIT files: pip install fitparse")

        track_points = []
        cue_points = []

        # Detect file type
        file_type = 'unknown'
        fitfile = FitFile(str(self.filepath))

        for record in fitfile.get_messages('file_id'):
            for field in record:
                if field.name == 'type':
                    file_type = field.value
                    break

        # Parse trackpoints
        fitfile = FitFile(str(self.filepath))
        for record in fitfile.get_messages('record'):
            lat, lon, ele, dist, timestamp = None, None, None, None, None

            for field in record:
                if field.name == 'position_lat':
                    lat = field.value * (180 / 2**31) if field.value else None
                elif field.name == 'position_long':
                    lon = field.value * (180 / 2**31) if field.value else None
                elif field.name == 'altitude':
                    ele = field.value
                elif field.name == 'distance':
                    dist = field.value
                elif field.name == 'timestamp':
                    try:
                        timestamp = dt.timestamp(field.value)
                    except:
                        timestamp = np.nan

            if lat is not None and lon is not None:
                if ele is None:
                    ele = np.nan
                if dist is None:
                    dist = 0
                if timestamp is None:
                    timestamp = np.nan

                track_points.append((lat, lon, timestamp, dist, ele))

        # Parse course points
        if file_type == 'course':
            fitfile = FitFile(str(self.filepath))
            for record in fitfile.get_messages('course_point'):
                lat, lon, name, notes, cue_type, dist, timestamp = None, None, '', '', '', 0, np.nan

                for field in record:
                    if field.name == 'position_lat':
                        lat = field.value * (180 / 2**31) if field.value else None
                    elif field.name == 'position_long':
                        lon = field.value * (180 / 2**31) if field.value else None
                    elif field.name == 'name':
                        name = field.value if field.value else ''
                    elif field.name == 'notes':
                        notes = field.value if field.value else ''
                    elif field.name == 'type':
                        cue_type = field.value if field.value else ''
                    elif field.name == 'distance':
                        dist = field.value if field.value else 0
                    elif field.name == 'timestamp':
                        try:
                            timestamp = dt.timestamp(field.value)
                        except:
                            timestamp = np.nan

                if lat is not None and lon is not None:
                    cue_points.append((lat, lon, timestamp, dist, name, cue_type, notes))

        self._set_parsed(track_points, cue_points)

    def _parse_rwgps_json(self):
        """
        Parse a RideWithGPS API JSON response (route or trip).
        """
        with open(self.filepath, 'r') as f:
            data = json.load(f)

        if 'route' in data:
            asset = data['route']
            is_trip = False
        elif 'trip' in data:
            asset = data['trip']
            is_trip = True
        else:
            raise ValueError(f"Unrecognized RideWithGPS JSON structure in {self.filepath}: "
                              f"expected top-level 'route' or 'trip' key.")

        track_points = []
        for pt in asset.get('track_points', []):
            lat = pt.get('y')
            lon = pt.get('x')
            if lat is None or lon is None:
                continue
            dist = pt.get('d', 0) or 0
            ele = pt.get('e')
            if ele is None:
                ele = np.nan
            timestamp = pt.get('t') if is_trip else None
            if timestamp is None:
                timestamp = np.nan
            track_points.append((lat, lon, timestamp, dist, ele))

        cue_points = []
        for cp in asset.get('course_points', []):
            lat = cp.get('y')
            lon = cp.get('x')
            if lat is None or lon is None:
                continue
            dist = cp.get('d', 0) or 0
            cue_points.append((lat, lon, np.nan, dist, cp.get('n', '') or '', cp.get('t', '') or '',
                               'User-edited cue' if cp.get('_e') else ''))

        if not track_points:
            raise ValueError(f"No track points found in RideWithGPS response {self.filepath}.")

        self._set_parsed(track_points, cue_points)


    def _assign_crs_distances(self):
        """Take each cue's distance from the track point at the same position (and time, if known)."""
        if self.cues.height == 0:
            return
        lat, lon, timestamp, distance = self['lat'], self['lon'], self['timestamp'], self['distance_m']
        cue_distance = self.cues['distance_m'].to_numpy(writable=True)
        for j, cue in enumerate(self.cues.iter_rows(named=True)):
            same_position = (lat == cue['lat']) & (lon == cue['lon'])
            if not np.isnan(cue['timestamp']):
                same_position &= timestamp == cue['timestamp']
            match = np.flatnonzero(same_position)
            if len(match) > 0:
                cue_distance[j] = distance[match[0]]
        self.cues = self.cues.with_columns(distance_m=cue_distance)


# Example usage
if __name__ == "__main__":
    import sys
    from types import SimpleNamespace

    if len(sys.argv) < 2:
        print("Usage: python track.py <gps_file>")
        sys.exit(1)

    filepath = sys.argv[1]

    print(f"Loading {filepath}...")
    track = Track(SimpleNamespace(input=filepath))

    print(f"\nTrack points: {len(track)}")
    if len(track) > 0:
        print(f"  Total distance: {track['distance_m'][-1]/1000:.2f} km")
        print(track.points.head())

    print(f"\nCourse points: {track.cues.height}")
    print(track.cues.head())
