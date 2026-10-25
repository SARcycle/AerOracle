"""
TCX Writer - Write track points (track.points) and course points (track.cues) to TCX course files
"""

import subprocess
from datetime import datetime as dt, timezone
from pathlib import Path

import numpy as np
import xmltodict

_TURN_TYPES = {
    'sharp left': 'Left',
    'slight left': 'Left',
    'sharp right': 'Right',
    'slight right': 'Right',
}

MAX_COURSE_POINT_NAME_LEN = 10
# gpsbabel output format per file suffix (FIT courses: gpsbabel >= 1.10, course point types set by write_fit)
GPSBABEL_FORMATS = {'.gpx': 'gpx', '.fit': 'garmin_fit'}
MAX_COURSE_NAME_LEN = 15


def map_point_type(point_type: str) -> str:
    key = ' '.join(str(point_type).replace('-', ' ').replace('_', ' ').split()).lower()
    return _TURN_TYPES.get(key, point_type)


def format_time(timestamp: float) -> str:
    return dt.fromtimestamp(timestamp, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def trackpoints(track) -> list:
    """TCX <Trackpoint> entries of track.points."""
    entries = []
    for point in track.points.select('lat', 'lon', 'timestamp', 'distance_m', 'elevation_m').iter_rows(named=True):
        tp = {
            'Position': {
                'LatitudeDegrees': str(point['lat']),
                'LongitudeDegrees': str(point['lon'])
            },
            'DistanceMeters': str(point['distance_m']),
            'AltitudeMeters': str(point['elevation_m'] if not np.isnan(point['elevation_m']) else 0)
        }

        # Add time if available
        if not np.isnan(point['timestamp']):
            tp['Time'] = format_time(point['timestamp'])

        entries.append(tp)
    return entries


def snapped_cues(track) -> list:
    """
    track.cues as dicts (name, timestamp, lat, lon, type, notes) in course order:
    - Snaps course points to the matching (or else nearest) track point
    - Sorts by time
    """
    lat, lon, timestamp = track['lat'], track['lon'], track['timestamp']
    cues = []
    for cue in track.cues.iter_rows(named=True):
        trackpt_idx = np.flatnonzero((lat == cue['lat']) & (lon == cue['lon']))
        if len(trackpt_idx) > 0:
            # Snap to exact track point
            nearest_idx = trackpt_idx[0]
        else:
            # Find nearest track point if no exact match
            nearest_idx = np.argmin(np.sqrt((lat - cue['lat'])**2 + (lon - cue['lon'])**2))
        cues.append(dict(cue, lat=lat[nearest_idx], lon=lon[nearest_idx], timestamp=timestamp[nearest_idx]))
    cues.sort(key=lambda cue: -np.inf if np.isnan(cue['timestamp']) else cue['timestamp'])
    return cues


def coursepoints(track) -> list:
    """TCX <CoursePoint> entries of track.cues."""
    return [{
        'Name': cue['name'][:MAX_COURSE_POINT_NAME_LEN],
        'Time': format_time(cue['timestamp'] if not np.isnan(cue['timestamp']) else dt.now(timezone.utc).timestamp()),
        'Position': {
            'LatitudeDegrees': str(cue['lat']),
            'LongitudeDegrees': str(cue['lon'])
        },
        'PointType': map_point_type(cue['type']),
        'Notes': cue['notes']
    } for cue in snapped_cues(track)]


def update_tcx_name(filepath: str, new_name: str) -> None:
    """
    Update the course name in a TCX file.

    Args:
        filepath: Path to TCX file
        new_name: New course name
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        tcx_dict = xmltodict.parse(f.read())

    # Update name in all locations
    new_name = new_name[:MAX_COURSE_NAME_LEN] # Limit to max length
    tcx_dict['TrainingCenterDatabase']['Courses']['Course']['Name'] = new_name
    tcx_dict['TrainingCenterDatabase']['Folders']['Courses']['CourseFolder']['CourseNameRef']['Id'] = new_name

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(xmltodict.unparse(tcx_dict, pretty=True))

    print(f"- Course name: {new_name}")


def write_tcx(filepath: str, track, name: str | None = None) -> None:
    """
    Write track points and course points to a TCX course file.

    Args:
        filepath: Output TCX file path (suffix is replaced by .tcx)
        track: track.Track with points (lat, lon, timestamp, distance_m, elevation_m) and cues
        name: Course name (defaults to filename)
    """
    if name is None:
        name = Path(filepath).stem
        name = name[:MAX_COURSE_NAME_LEN]

    timestamp, distance = track['timestamp'], track['distance_m']
    course = {
        'Name': name,
        'Lap': {
            'DistanceMeters': distance[-1],
            'TotalTimeSeconds': float(timestamp[-1] - timestamp[0]),
            'Intensity': 'Active',
            'TriggerMethod': 'Manual'
        },
        'Track': {
            'Trackpoint': trackpoints(track)
        }
    }
    if track.cues.height > 0:
        course['CoursePoint'] = coursepoints(track)

    tcx_dict = {
        'TrainingCenterDatabase': {
            '@xmlns': 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2',
            '@xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
            '@xsi:schemaLocation': 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2 http://www.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd',
            'Courses': {
                'Course': course
            },
            'Folders': {
                'Courses': {
                    'CourseFolder': {
                        '@Name': 'My Courses',
                        'CourseNameRef': {
                            'Id': name
                        }
                    }
                }
            }
        }
    }

    # Write to file
    output_path = Path(filepath).with_suffix('.tcx')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(xmltodict.unparse(tcx_dict, pretty=True))


def convert(tcx_path, output_path) -> bool:
    """Convert a TCX course with gpsbabel, format by suffix of output_path. Returns False if gpsbabel is missing or fails."""
    try:
        subprocess.run(['gpsbabel', '-i', 'gtrnctr', '-f', str(tcx_path),
                        '-o', GPSBABEL_FORMATS[Path(output_path).suffix], '-F', str(output_path)],
                       check=True, capture_output=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


# FIT course_point.type values (FIT SDK profile, enum course_point)
FIT_COURSE_POINT_TYPES = {
    'generic': 0, 'summit': 1, 'valley': 2, 'water': 3, 'food': 4, 'danger': 5, 'left': 6, 'right': 7,
    'straight': 8, 'first_aid': 9, 'fourth_category': 10, 'third_category': 11, 'second_category': 12,
    'first_category': 13, 'hors_category': 14, 'sprint': 15, 'left_fork': 16, 'right_fork': 17,
    'middle_fork': 18, 'slight_left': 19, 'sharp_left': 20, 'slight_right': 21, 'sharp_right': 22, 'u_turn': 23,
    'segment_start': 24, 'segment_end': 25,
}
_FIT_TYPE_ALIASES = {'4th_category': 'fourth_category', '3rd_category': 'third_category', '2nd_category': 'second_category',
                     '1st_category': 'first_category', 'hors_categorie': 'hors_category', 'uturn': 'u_turn'}
FIT_COURSE_POINT_MESSAGE = 32
FIT_COURSE_POINT_TYPE_FIELD = 5


def fit_course_point_type(point_type: str) -> int:
    """FIT course point type for a cue type such as 'Slight Left', '1st Category' or 'Summit' (generic if unknown)."""
    key = '_'.join(str(point_type).replace('-', ' ').lower().split())
    return FIT_COURSE_POINT_TYPES.get(_FIT_TYPE_ALIASES.get(key, key), FIT_COURSE_POINT_TYPES['generic'])


def fit_crc(data: bytes, crc: int = 0) -> int:
    """CRC-16 of the FIT protocol."""
    table = (0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
             0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400)
    for byte in data:
        for nibble in (byte & 0xF, byte >> 4):
            tmp = table[crc & 0xF]
            crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ table[nibble]
    return crc


def set_fit_course_point_types(fit_path, types: list) -> bool:
    """
    Overwrite the type of the course_point messages of a FIT file in file order with types (FIT enum values)
    and update the file CRC. Returns False (file unchanged) if the number of course points does not match.
    """
    data = bytearray(Path(fit_path).read_bytes())
    header_size = data[0]
    end = header_size + int.from_bytes(data[4:8], 'little')
    type_offsets = []   # Byte positions of the course point type values
    definitions = {}    # Local message type -> (global message number, data size, offset of the type field or None)
    pos = header_size
    while pos < end:
        record_header = data[pos]
        pos += 1
        if record_header & 0x80:  # Compressed timestamp header: data message
            local_type = (record_header >> 5) & 0x3
        elif record_header & 0x40:  # Definition message
            local_type = record_header & 0x0F
            big_endian = data[pos + 1] == 1
            global_number = int.from_bytes(data[pos + 2:pos + 4], 'big' if big_endian else 'little')
            field_count = data[pos + 4]
            fields = [data[pos + 5 + 3 * i:pos + 8 + 3 * i] for i in range(field_count)]
            pos += 5 + 3 * field_count
            size, type_offset = 0, None
            for number, field_size, _ in fields:
                if number == FIT_COURSE_POINT_TYPE_FIELD:
                    type_offset = size
                size += field_size
            if record_header & 0x20:  # Developer fields
                developer_count = data[pos]
                size += sum(data[pos + 2 + 3 * i] for i in range(developer_count))
                pos += 1 + 3 * developer_count
            definitions[local_type] = (global_number, size, type_offset)
            continue
        else:  # Normal data message
            local_type = record_header & 0x0F
        global_number, size, type_offset = definitions[local_type]
        if global_number == FIT_COURSE_POINT_MESSAGE and type_offset is not None:
            type_offsets.append(pos + type_offset)
        pos += size

    if len(type_offsets) != len(types):
        return False
    for offset, point_type in zip(type_offsets, types):
        data[offset] = point_type
    data[end:end + 2] = fit_crc(data[:end]).to_bytes(2, 'little')
    Path(fit_path).write_bytes(data)
    return True


def write_fit(tcx_path, fit_path, track) -> bool:
    """FIT course from the TCX file (via gpsbabel), with the course point types of track.cues. Returns False if gpsbabel fails."""
    if not convert(tcx_path, fit_path):
        return False
    if not set_fit_course_point_types(fit_path, [fit_course_point_type(cue['type']) for cue in snapped_cues(track)]):
        print('- FIT course point types could not be set (generic)')
    return True


def export(tcx_path, output_path, track) -> bool:
    """GPX or FIT (by suffix of output_path) from the TCX course. Returns False if gpsbabel is missing or fails."""
    if Path(output_path).suffix == '.fit':
        return write_fit(tcx_path, output_path, track)
    return convert(tcx_path, output_path)
