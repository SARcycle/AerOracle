import re
import pandas as pd

import requests
from pygeodesy.ellipsoidalVincenty import LatLon

def create_csv(csv_path, buffer_distance_meters):
    url = 'https://download.geofabrik.de/index-v1.json'
    extracted_data = fetch_and_extract(url, buffer_distance_meter=buffer_distance_meters)
    df = pd.DataFrame(data={
        'name_descr': [d['name_descr'] for d in extracted_data],
        'name': [d['name'] for d in extracted_data],
        'url': [d['osm_url'] for d in extracted_data],
        'buffer': [d['buffer_distance_deg'] for d in extracted_data]
    })
    df = df.sort_values('name')
    df.to_csv(csv_path, sep=';', index=False)

def fetch_and_extract(url, buffer_distance_meter):
    # Fetch the JSON data from the URL
    response = requests.get(url)
    data = response.json()

    # Initialize a list to store all results
    all_results = []

    # Iterate through the features in the JSON data
    for feature in data.get('features', []):
        properties = feature.get('properties', {})
        taginfo = properties.get('urls', {}).get('taginfo', '')
        osm_url = properties.get('urls', {}).get('pbf', '')
        geometry = feature.get('geometry', {})

        # Check the number of levels in taginfo
        if taginfo:
            taginfo = taginfo.replace('geofabrik.de/russia', 'geofabrik.de/europe:russia')
            levels = taginfo.split('/')[-1].split(':')  # Get the last part of the URL and split by ':'
        else:
            continue  # Skip if there's no taginfo URL

        # Check the number of levels
        num_levels = len(levels)

        # Only consider features with up to three levels
        if num_levels > 3:
            continue

        taginfo_filter_keys = [
            'europe:alps',
            'europe:britain-and-ireland',
            'europe:dach',
            'europe:great-britain',
            'africa:south-africa-and-lesotho',
            'north-america:us-midwest',
            'north-america:us-northeast',
            'north-america:us-pacific',
            'north-america:us-south',
            'north-america:us-west',
            'asia:sea'
        ]

        if any(taginfo_filter_key in str.join(':', levels) for taginfo_filter_key in taginfo_filter_keys):
            continue

        # Extract the coordinates from the geometry
        coordinates = geometry.get('coordinates', [])
        if not coordinates:
            continue  # Skip if there are no coordinates

        # Flatten the list of coordinates (assuming MultiPolygon)
        flat_coordinates = [point for polygon in coordinates for ring in polygon for point in ring]

        # Calculate the mean latitude
        latitudes = [point[1] for point in flat_coordinates]  # Latitude is the second element
        mean_latitude = (max(latitudes)+min(latitudes))/2 if latitudes else 0
        longitudes = [point[0] for point in flat_coordinates]  # Longitude is the second element
        mean_longitude = (max(longitudes)+min(longitudes))/2 if longitudes else 0

        # Calculate the length of distance buffer_distance_meter based on the mean latitude/longitude
        delta_lat = 0.25
        buffer_distance_deg = (delta_lat*buffer_distance_meter)/LatLon(mean_latitude, mean_longitude).distanceTo(LatLon(mean_latitude+delta_lat, mean_longitude))

        if len(levels)<=2:
            name_descr = re.sub(r'\b[a-z]', lambda m: m.group().upper(), levels[-1].replace('-',' '))
            name = levels[-1].replace('-','')
        else:
            name_descr = re.sub(r'\b[a-z]', lambda m: m.group().upper(), f'{levels[1].replace("-", " ")} ({levels[-1].replace("-", " ")})')
            if 'Us (' in name_descr:
                name_descr = name_descr.replace('Us', 'US')
            name = f'{levels[1].replace("-", "")}_{levels[-1].replace("-", "")}'


        # Store the result
        all_results.append({
            'name_descr': name_descr,
            'name': name,
            'osm_url': osm_url,
            #'taginfo': taginfo,
            #'mean_latitude': mean_latitude,
            #'mean_longitude': mean_longitude,
            'buffer_distance_deg': buffer_distance_deg,
            'levels': levels  # Store the levels directly
        })

    # Separate results by number of levels
    one_level_results = [result for result in all_results if len(result['levels']) == 1]
    two_level_results = [result for result in all_results if len(result['levels']) == 2]
    three_level_results = [result for result in all_results if len(result['levels']) == 3]

    # Function to filter results based on more specific entries
    def filter_results(results, more_specific_results):
        to_remove = []
        for result in results:
            last_level = result['levels'][-1]
            # Check if there are more specific results containing the same first level
            if any(last_level in more_specific['levels'] for more_specific in more_specific_results):
                to_remove.append(result)
        return [result for result in results if result not in to_remove]

    # Filter one-level results based on two-level results
    one_level_results = filter_results(one_level_results, two_level_results)
    # Filter two-level results based on three-level results
    two_level_results = filter_results(two_level_results, three_level_results)

    # Combine the filtered results
    final_results = one_level_results + two_level_results + three_level_results

    return final_results  # Return the final results
