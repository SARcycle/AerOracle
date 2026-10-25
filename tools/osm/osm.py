import glob
import sys
import time

import os
import requests
import wget

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))  # Project root: config, utilities
import config
import utilities

TMP_DIR = os.path.join(config.TEMP_DIR, 'OSM')  # Extracts of up to a few GB: temp_dir must be on disk, not a small tmpfs
MULTI2SINGLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'multi2single.py')


def download_extract_buffer_tunnels(ctry_name, continentcntry, url, savepath=None, searchparameters='--keep-ways= --keep-relations= --keep=" ( highway=*ary* =unclassified =residential =living_street =pedestrian =road =cycleway ) and tunnel=yes" --drop="bicycle=no"', bufferdistance=0.000354):

    tmp_folder = TMP_DIR

    tmp_folder_osm = tmp_folder+'/RAW'
    tmp_folder_shp = tmp_folder+'/SHP'

    if not os.path.isdir(tmp_folder_osm):
        os.makedirs(tmp_folder_osm)
    if not os.path.isdir(tmp_folder_shp):
        os.makedirs(tmp_folder_shp)
    country = url[url.rfind("/") + 1:url.find("-latest")]
    print('Working on {}'.format(ctry_name))
    download_osm_file(url, tmp_folder_osm, country)

    print("\t- Extracting OSM Data")
    cmdstr = "osmfilter {}/{}-latest.o5m {} -o={}/{}-extract.osm > /dev/null 2>&1".format(tmp_folder_osm, country, searchparameters, tmp_folder_osm, continentcntry)
    os.system(cmdstr)
    os.remove("{}/{}-latest.o5m".format(tmp_folder_osm, country))
#    print('\t- Buffering')
#    print('\t\t- Lines')
    print("\t- Converting to SHP")
    if savepath is not None: # if working on world
        cmdstr = 'ogr2ogr {}/{}.shp {}/{}-extract.osm -simplify 0.00006 -dialect sqlite -sql \"SELECT ST_Union(ST_Buffer(geometry, {})) AS geometry,* FROM \'lines\'\" -f \"ESRI Shapefile\" > /dev/null 2>&1'.format(
            savepath, continentcntry, tmp_folder_osm, continentcntry, bufferdistance)
    else:
        cmdstr = 'ogr2ogr {}/{}_l.shp {}/{}-extract.osm -simplify 0.00006 -dialect sqlite -sql \"SELECT ST_Union(ST_Buffer(geometry, {})) AS geometry,* FROM \'lines\'\" -f \"ESRI Shapefile\" > /dev/null 2>&1'.format(tmp_folder_shp, continentcntry, tmp_folder_osm, continentcntry, bufferdistance)
#    cmdstr = 'ogr2ogr {}/{}_l.shp {}/{}-extract.osm -a_srs +init="epsg:4326" simplify 0.00006 -dialect sqlite -sql \"SELECT ST_Union(ST_Buffer(geometry, {})) AS geometry,* FROM \'lines\'\" -f \"ESRI Shapefile\"'.format(tmp_folder_shp, country, tmp_folder_osm, country, bufferdistance)
#    print(cmdstr)
#    print('Waiting')
#    time.sleep(5)
    os.system(cmdstr)
    #    print('\t\t- MultiLineStrings')
#    cmdstr = 'ogr2ogr {}/{}_mls.shp {}/{}-extract.osm -dialect sqlite -sql \"SELECT ST_Union(ST_Buffer(geometry, {})) AS geometry,* FROM \'multilinestrings\'\" -f \"ESRI Shapefile\"'.format(savepath, country, tmp_folder, country, bufferdistance)
#    os.system(cmdstr)
    os.remove('{}/{}-extract.osm'.format(tmp_folder_osm, continentcntry))

def download_extract_buffer(ctry_name, continentcntry, url, search_object, savepath=None, bufferdistance=0.000354):
    if search_object == 'tunnels':
        searchparameters = '--keep-ways= --keep-relations= --keep=" ( highway=*ary* =unclassified =residential =living_street =pedestrian =road =cycleway ) and tunnel=yes" --drop="bicycle=no"'
    elif search_object == 'bridges':
        searchparameters = '--keep-ways= --keep-relations= --keep=" ( highway=*ary* =unclassified =residential =living_street =pedestrian =road =cycleway ) and bridge=*" --drop="bicycle=no"'

    tmp_folder = TMP_DIR

    tmp_folder_osm = os.path.join(tmp_folder, 'RAW')
    tmp_folder_shp = os.path.join(tmp_folder, 'SHP', search_object)

    if not os.path.isdir(tmp_folder_osm):
        os.makedirs(tmp_folder_osm)
    if not os.path.isdir(tmp_folder_shp):
        os.makedirs(tmp_folder_shp)
    # if(not os.path.isdir(savepath)):
    #     os.makedirs(savepath)

    country = url[url.rfind("/") + 1:url.find("-latest")]
    download_osm_file(url, tmp_folder_osm, country)
    print(f'\t- {search_object.capitalize()}')
    print(f"\t\t- Extracting OSM Data")
    start_size = utilities.sizeof_fmt(
        sum(os.path.getsize(fn) for fn in sorted(glob.glob(os.path.join(tmp_folder_osm, f'{country}-latest.o5m')))))
    cmdstr = "osmfilter {}/{}-latest.o5m {} -o={}/{}-extract.osm > /dev/null 2>&1".format(tmp_folder_osm, country, searchparameters, tmp_folder_osm, continentcntry)
    start_time = time.time()
    os.system(cmdstr)
    end_size = utilities.sizeof_fmt(
        sum(os.path.getsize(fn) for fn in sorted(glob.glob(os.path.join(tmp_folder_osm, f'{continentcntry}-extract.osm')))))
    print(f'\t\t\t- {start_size} --> {end_size}; {time.time()-start_time:0.3f} s')
    #    print('\t- Buffering')
#    print('\t\t- Lines')
    if savepath is not None: # if working on world
        if not os.path.exists(savepath):
            os.makedirs(savepath)
        if search_object == 'bridges':
            print(f"\t\t- Converting to SHP")
            start_size = utilities.sizeof_fmt(sum(os.path.getsize(fn) for fn in sorted(glob.glob(os.path.join(tmp_folder_osm, f'{continentcntry}-extract.osm')))))
            cmdstr = 'ogr2ogr {}/{}_raw.shp {}/{}-extract.osm -simplify 0.00006 -f \"ESRI Shapefile\" lines > /dev/null 2>&1'.format(
                tmp_folder_shp, continentcntry, tmp_folder_osm, continentcntry)
            start_time = time.time()
            os.system(cmdstr)
            end_size = utilities.sizeof_fmt(sum(os.path.getsize(fn) for fn in sorted(glob.glob(os.path.join(tmp_folder_shp, f'{continentcntry}_raw.*')))))
            print(f'\t\t\t- {start_size} --> {end_size}; {time.time()-start_time:0.3f} s')
            os.remove('{}/{}-extract.osm'.format(tmp_folder_osm, continentcntry))

            length_filtering(os.path.join(tmp_folder_shp,f'{continentcntry}_raw.shp'), os.path.join(tmp_folder_shp,f'{continentcntry}_filtered.shp'), 50)

            print(f"\t\t- Cleaning attribute table")
            start_size = utilities.sizeof_fmt(
                sum(os.path.getsize(fn) for fn in sorted(glob.glob(os.path.join(tmp_folder_shp, f'{continentcntry}_filtered.*')))))
            cmdstr = f'ogr2ogr {tmp_folder_shp}/{continentcntry}_clean.shp {tmp_folder_shp}/{continentcntry}_filtered.shp -dialect sqlite -sql "SELECT geometry FROM \'{continentcntry}_filtered\'" > /dev/null 2>&1'
            start_time = time.time()
            os.system(cmdstr)
            end_size = utilities.sizeof_fmt(
                sum(os.path.getsize(fn) for fn in sorted(glob.glob(os.path.join(tmp_folder_shp, f'{continentcntry}_clean.*')))))
            print(f'\t\t\t- {start_size} --> {end_size}; {time.time()-start_time:0.3f} s')
            os.system(f'rm {tmp_folder_shp}/{continentcntry}_filtered.*')

            print(f"\t\t- Buffering features")
            start_size = end_size
            cmdstr = 'ogr2ogr {}/{}_buffered.shp {}/{}_clean.shp -simplify 0.00006 -dialect sqlite -sql \"SELECT ST_Buffer(geometry, {}) AS geometry,* FROM \'{}_clean\'\" -f \"ESRI Shapefile\"'.format(
            tmp_folder_shp, continentcntry, tmp_folder_shp, continentcntry, bufferdistance, continentcntry)
            start_time = time.time()
            os.system(cmdstr)
            end_size = utilities.sizeof_fmt(sum(os.path.getsize(fn) for fn in sorted(glob.glob(os.path.join(tmp_folder_shp, f'{continentcntry}_buffered.*')))))
            print(f'\t\t\t- {start_size} --> {end_size}; {time.time()-start_time:0.3f} s')
            os.system(f'rm {tmp_folder_shp}/{continentcntry}_clean.*')

            print(f"\t\t- Dissolving features")
            start_size = end_size
            cmdstr = 'ogr2ogr {}/{}.shp {}/{}_buffered.shp -simplify 0.00006 -dialect sqlite -sql \"SELECT ST_Union(geometry) AS geometry,* FROM \'{}_buffered\'\" -f \"ESRI Shapefile\"'.format(
            savepath, continentcntry, tmp_folder_shp, continentcntry, continentcntry)
            start_time = time.time()
            os.system(cmdstr)
            end_size = utilities.sizeof_fmt(sum(os.path.getsize(fn) for fn in sorted(glob.glob(os.path.join(savepath, f'{continentcntry}.*')))))
            print(f'\t\t\t- {start_size} --> {end_size}; {time.time()-start_time:0.3f} s')
            os.system(f'rm {tmp_folder_shp}/{continentcntry}_buffered.*')
        else:
            print(f"\t\t- Converting to SHP & cleaning attribute table")
            start_size = utilities.sizeof_fmt(sum(
                os.path.getsize(fn) for fn in sorted(glob.glob(os.path.join(tmp_folder_osm, f'{continentcntry}-extract.osm')))))
            cmdstr = 'ogr2ogr {}/{}_raw.shp {}/{}-extract.osm -simplify 0.00006 -f \"ESRI Shapefile\" lines > /dev/null 2>&1'.format(
                tmp_folder_shp, continentcntry, tmp_folder_osm, continentcntry)
            start_time = time.time()
            os.system(cmdstr)
            end_size = utilities.sizeof_fmt(
                sum(os.path.getsize(fn) for fn in sorted(glob.glob(os.path.join(tmp_folder_shp, f'{continentcntry}_raw.*')))))
            print(f'\t\t\t- {start_size} --> {end_size}; {time.time() - start_time:0.3f} s')
            os.remove('{}/{}-extract.osm'.format(tmp_folder_osm, continentcntry))

            print(f"\t\t- Buffering features")
            start_size = end_size
            cmdstr = 'ogr2ogr {}/{}_buffered.shp {}/{}_raw.shp -simplify 0.00006 -dialect sqlite -sql \"SELECT ST_Buffer(geometry, {}) AS geometry,* FROM \'{}_raw\'\" -f \"ESRI Shapefile\"'.format(
                tmp_folder_shp, continentcntry, tmp_folder_shp, continentcntry, bufferdistance, continentcntry)
            start_time = time.time()
            os.system(cmdstr)
            end_size = utilities.sizeof_fmt(sum(
                os.path.getsize(fn) for fn in sorted(glob.glob(os.path.join(tmp_folder_shp, f'{continentcntry}_buffered.*')))))
            print(f'\t\t\t- {start_size} --> {end_size}; {time.time() - start_time:0.3f} s')
            os.system(f'rm {tmp_folder_shp}/{continentcntry}_raw.*')

            print(f"\t\t- Dissolving features")
            start_size = end_size
            cmdstr = 'ogr2ogr {}/{}.shp {}/{}_buffered.shp -simplify 0.00006 -dialect sqlite -sql \"SELECT ST_Union(geometry) AS geometry,* FROM \'{}_buffered\'\" -f \"ESRI Shapefile\"'.format(
                savepath, continentcntry, tmp_folder_shp, continentcntry, continentcntry)
            start_time = time.time()
            os.system(cmdstr)
            end_size = utilities.sizeof_fmt(
                sum(os.path.getsize(fn) for fn in sorted(glob.glob(os.path.join(savepath, f'{continentcntry}.*')))))
            print(f'\t\t\t- {start_size} --> {end_size}; {time.time() - start_time:0.3f} s')
            os.system(f'rm {tmp_folder_shp}/{continentcntry}_buffered.*')

    else:
        cmdstr = 'ogr2ogr {}/{}_l.shp {}/{}-extract.osm -simplify 0.00006 -dialect sqlite -sql \"SELECT ST_Union(ST_Buffer(geometry, {})) AS geometry,* FROM \'lines\'\" -f \"ESRI Shapefile\" > /dev/null 2>&1'.format(tmp_folder_shp, continentcntry, tmp_folder_osm, continentcntry, bufferdistance)
        os.system(cmdstr)

def download_extract_buffer_tunnels_bridges(ctry_name, continentcntry, url, savepath=None, searchparameters='--keep-ways= --keep-relations= --keep=" ( highway=*ary* =unclassified =living_street =road =cycleway ) and ( tunnel=yes or bridge=* )" --drop="bicycle=no"', bufferdistance=0.000354):

    tmp_folder = os.path.join(TMP_DIR, 'Tunnels')

    tmp_folder_osm = tmp_folder+'/OSM'
    tmp_folder_shp = tmp_folder+'/SHP'

    if not os.path.isdir(tmp_folder_osm):
        os.makedirs(tmp_folder_osm)
    if not os.path.isdir(tmp_folder_shp):
        os.makedirs(tmp_folder_shp)
    if not os.path.isdir(savepath):
        os.makedirs(savepath)

    country = url[url.rfind("/") + 1:url.find("-latest")]

    print("Working on {}".format(ctry_name))
    download_osm_file(url, tmp_folder_osm, country)
    print("\t- Extracting OSM Data")
    cmdstr = "osmfilter {}/{}-latest.o5m {} -o={}/{}-extract.osm".format(tmp_folder_osm, country, searchparameters, tmp_folder_osm, continentcntry)
    os.system(cmdstr)
    os.remove("{}/{}-latest.o5m".format(tmp_folder_osm, country))

    print("\t- Converting to SHP")
    # Select all line features
    print("\t\t- Selecting/buffering Features")
    select_cmd = f'ogr2ogr {tmp_folder_shp}/{continentcntry}_clean.shp {tmp_folder_osm}/{continentcntry}-extract.osm -simplify 0.00006 -dialect sqlite -sql "SELECT geometry FROM \'lines\'"'
    os.system(select_cmd)
    os.system(f'rm {tmp_folder_osm}/{continentcntry}-extract.osm')
    select_cmd = f'ogr2ogr {tmp_folder_shp}/{continentcntry}_buffered.shp {tmp_folder_shp}/{continentcntry}_clean.shp -simplify 0.00006 -dialect sqlite -sql "SELECT ST_Buffer(geometry, {bufferdistance}) AS geometry,* FROM \'{continentcntry}_clean\'"'
    os.system(select_cmd)
    os.system(f'rm {tmp_folder_shp}/{continentcntry}_clean.*')

    # Dissolve the buffered features
    print("\t\t- Dissolving Features")
    dissolve_cmd = f'ogr2ogr {savepath}/{continentcntry}.shp {tmp_folder_shp}/{continentcntry}_buffered.shp -dialect sqlite -sql "SELECT ST_Union(geometry) AS geometry,* FROM \'{continentcntry}_buffered\'"'
    os.system(dissolve_cmd)
    os.system(f'rm {tmp_folder_shp}/{continentcntry}_buffered.*')

def download_country_extents():
    import csv

    folder_prefix = config.DATA_DIR
    tmp_folder = TMP_DIR

    with open('{}/GeoData/OSM/osm_all.csv'.format(folder_prefix)) as csv_file:
        csv_reader = csv.reader(csv_file, delimiter=';')
        next(csv_reader)  # Skipping header line
        for row in csv_reader:
            print('Downloading kml for {}'.format(row[0]))
            if not os.path.isdir(tmp_folder):
                os.makedirs(tmp_folder)

            wget.download(row[2].replace('-latest.osm.pbf', '.kml'), out='{}/kml'.format(tmp_folder), bar=None)

def download_kml(url, output_path):
    try:
        response = requests.get(url)
        if response.status_code == 200:
            with open(output_path, 'wb') as f:
                f.write(response.content)
            print(f"Downloaded: {output_path}")
        else:
            print(f"Failed to download: {url}")
    except Exception as e:
        print(f"Error downloading {url}: {e}")


def convert_kml_to_shp(kml_file):
    shp_file = kml_file.replace(".kml", ".shp")
    ctry_name = os.path.basename(kml_file).replace('.kml', '')
    ogr_cmd = f'ogr2ogr {shp_file} {kml_file} -makevalid -simplify 0.00006 -dialect sqlite -sql "SELECT geometry FROM \'{ctry_name}\'" > /dev/null 2>&1'
    os.system(ogr_cmd)
    return shp_file


def merge_shapefiles(input_folder):
    shp_files = sorted(glob.glob(os.path.join(input_folder, "*.shp")))
    grouped_files = {}
    for shp_file in shp_files:
        if "_merged" not in os.path.basename(shp_file):
            filename = os.path.basename(shp_file).split("_")[0]
            if filename not in grouped_files:
                grouped_files[filename] = []
            grouped_files[filename].append(shp_file)

    for filename, files in grouped_files.items():
        output_merged = os.path.join(input_folder, f"{filename.replace('.shp', '')}_merged.shp")

        # # Create a new shapefile to store the merged geometries
        # driver = ogr.GetDriverByName('ESRI Shapefile')
        #
        # # Get the spatial reference from the first shapefile
        # dataSource = driver.Open(files[0], 0)
        # layerIn = dataSource.GetLayer()
        # spatialRef = layerIn.GetSpatialRef()
        #
        # ds = driver.CreateDataSource(output_merged)
        # layer = ds.CreateLayer('', spatialRef, ogr.wkbPolygon)
        # layer.CreateField(ogr.FieldDefn('id', ogr.OFTInteger))
        #
        # # Merge the geometries
        # for i, shp_file in enumerate(files):
        #     dataSource = driver.Open(shp_file, 0)
        #     layerIn = dataSource.GetLayer()
        #     for feat in layerIn:
        #         geom = feat.GetGeometryRef()
        #         defn = layer.GetLayerDefn()
        #         feat = ogr.Feature(defn)
        #         feat.SetField('id', i)
        #         feat.SetGeometry(geom.Clone())
        #         layer.CreateFeature(feat)
        # ds = None

        #cmd = f'ogr2ogr -f "ESRI Shapefile" -a_srs EPSG:4326 -t_srs EPSG:4326 {output_merged} {filename}'
        print(f'\t- {filename.replace(".shp","").capitalize()}')
        cmd = f'ogrmerge.py -single -overwrite_ds -o {output_merged} {os.path.join(input_folder,filename.replace(".shp", "")+"*.shp")} > /dev/null 2>&1'
        start_time = time.time()
        os.system(cmd)
        start_size = utilities.sizeof_fmt(sum(os.path.getsize(fn) for fn in sorted(glob.glob(os.path.join(input_folder,filename.replace(".shp", "")+"*.shp")))))
        end_size = utilities.sizeof_fmt(sum(os.path.getsize(fn) for fn in sorted(glob.glob(output_merged))))
        print(f'\t\t- {start_size} --> {end_size}; {time.time()-start_time:0.3f} s')

    #all_files = glob.glob(os.path.join(input_folder, '*'))

    # iterate over all the files
    #for file in all_files:
        # if "_merged" not in filename, delete it
    #    if "_merged" not in os.path.basename(file) and not os.path.isdir(file):
    #        os.remove(file)


def outlines_from_kml(csv_path):
    from zipfile import ZipFile
    import time

    tmp_folder = os.path.join(TMP_DIR, 'Tunnels')
    save_folder = config.DATA_DIR

    output_folder = f"{save_folder}/GeoData/OSM/outlines"

    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    print('Archiving old files (Outlines)')
    if not os.path.exists(os.path.join(output_folder, 'old')):
        os.makedirs(os.path.join(output_folder, 'old'))
    with ZipFile(os.path.join(output_folder, 'old', f'{time.strftime("%Y%m%d-%H%M%S")}.zip'), 'w') as zipObj2:
        for filename in sorted(glob.glob(os.path.join(output_folder, '*_MultiPoly.*'))):
            zipObj2.write(filename)
    file_list = sorted(glob.glob(f'{os.path.join(output_folder, "*.*")}'))
    for file in file_list:
        os.remove(file)
    os.system(f'ls {os.path.join(output_folder, "old", "*.zip")} -1t | tail -n +4 | xargs rm -f')

    with open(csv_path, 'r') as f:
        lines = f.readlines()
        for line in lines[1:]:  # Skip header
            parts = line.strip().split(";")
            name_descr, name, url, buffer_distance = parts
            kml_url = url.replace("-latest.osm.pbf", ".kml")
            kml_output_path = os.path.join(output_folder, f"{name}.kml")
            download_kml(kml_url, kml_output_path)
            shp_file = convert_kml_to_shp(kml_output_path)
            print(f"Converted {kml_output_path} to {shp_file}")

    merge_shapefiles(output_folder)
    merged_files = sorted(glob.glob(os.path.join(output_folder, '*_merged.shp')))
    for file in merged_files:
        print(f'Dissolving {os.path.basename(file).replace("_merged.shp", "").capitalize()}')
        # Old version
        # cmd = f'ogr2ogr {file.replace(".shp", "_dissolved.shp")} {file} -nlt PROMOTE_TO_MULTI -dialect sqlite -sql "SELECT ST_Union(geometry) AS geometry FROM """{os.path.basename(file.replace(".shp", ""))}"""" -f "ESRI Shapefile"'
        cmd = f'ogr2ogr {file.replace(".shp", "_dissolved.shp")} {file} -nlt PROMOTE_TO_MULTI -dialect sqlite -sql "SELECT ST_Union(ST_Buffer(geometry, 0)) AS geometry FROM """{os.path.basename(file.replace(".shp", ""))}"""" -f "ESRI Shapefile"'
        os.system(cmd)

    dissolved_files = sorted(glob.glob(os.path.join(output_folder, '*_dissolved.shp')))
    for file in dissolved_files:
        print(f'Multipart -> Singleparts ({os.path.basename(file).replace("_merged_dissolved.shp", "").capitalize()})')
        os.system(
            f'{sys.executable} {MULTI2SINGLE} {file} {file.replace(".shp", "_MultiPoly.shp")}')

    all_files = sorted(glob.glob(os.path.join(output_folder, '*')))

    # iterate over all the files
    for file in all_files:
        if "MultiPoly" not in os.path.basename(file) and not os.path.isdir(file):
            os.remove(file)

    # Favourizing chaecking these outlines first
    favourites = ['switzerland', 'liechtenstein', 'germany', 'france', 'italy', 'austria', 'spain', 'canaryislands']
    fav_counter = 0
    all_files = sorted(glob.glob(os.path.join(output_folder, '*')))
    for fav in favourites:
        fav_counter +=1
        for file in all_files:
            if fav in file:
                os.rename(file, file.replace(fav, f'{fav_counter}_{fav}'))

# Old
# def download_osm_file(url, tmp_folder_osm, country):
#     if os.path.exists(os.path.join(tmp_folder_osm, f'{country}-latest.o5m')) and time.time()-os.path.getmtime(os.path.join(tmp_folder_osm, f'{country}-latest.o5m')) < 86400: # Check if file older than a day
#         if os.path.exists(os.path.join(tmp_folder_osm, os.path.basename(url))):
#             os.system(f'rm {os.path.join(tmp_folder_osm, os.path.basename(url))}') # Original osm.pbf file not necessary any more
#     else:
#         if os.path.exists(os.path.join(tmp_folder_osm, os.path.basename(url))) and time.time()-os.path.getmtime(os.path.join(tmp_folder_osm, os.path.basename(url))) < 86400:
#             pass
#         else:
#             if os.path.exists(os.path.join(tmp_folder_osm, os.path.basename(url))):
#                 os.system(f'rm {os.path.join(tmp_folder_osm, os.path.basename(url))}') # Original osm.pbf file too old: not necessary any more
#             print("\t- Downloading OSM Data")
#             start_time = time.time()
#             wget.download(url, out=tmp_folder_osm, bar=None)
#             end_time = time.time()
#             file_size_raw = utilities.sizeof_fmt(os.path.getsize(os.path.join(tmp_folder_osm, os.path.basename(url))))
#             print(f'\t\t- {file_size_raw} in {end_time - start_time:0.3f} s [{(os.path.getsize(os.path.join(tmp_folder_osm, os.path.basename(url)))/(1024*1024)) / (end_time - start_time):0.3f} MB/s]')
#         print("\t- Converting OSM Data")
#         file_size_raw = utilities.sizeof_fmt(os.path.getsize(os.path.join(tmp_folder_osm, os.path.basename(url))))
#         cmdstr = "osmconvert {}/{}-latest.osm.pbf -o={}/{}-latest.o5m".format(tmp_folder_osm, country, tmp_folder_osm,
#                                                                               country)
#         start_time = time.time()
#         os.system(cmdstr)
#         file_size_conv = utilities.sizeof_fmt(os.path.getsize(os.path.join(tmp_folder_osm, os.path.basename(url).replace('osm.pbf', 'o5m'))))
#         print(f'\t\t- {file_size_raw} --> {file_size_conv}; {time.time()-start_time:0.3f} s')
#         os.remove("{}/{}-latest.osm.pbf".format(tmp_folder_osm, country))

def download_osm_file(url, tmp_folder_osm, country, max_retries=5):
    import hashlib
    
    def calculate_md5(filepath):
        """Calculate MD5 checksum of a file"""
        hash_md5 = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def download_with_verification(url, output_path, max_retries=5):
        """Download file and verify with MD5 checksum"""
        md5_url = url + ".md5"
        
        for attempt in range(max_retries):
            try:
                # Download the main file
                print(f"\t\t- Download attempt {attempt + 1}/{max_retries}")
                
                # Use requests instead of wget for better control
                response = requests.get(url, stream=True, timeout=300)
                response.raise_for_status()
                
                total_size = int(response.headers.get('content-length', 0))
                
                with open(output_path, 'wb') as f:
                    downloaded = 0
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                
                # Download and check MD5
                try:
                    md5_response = requests.get(md5_url, timeout=30)
                    md5_response.raise_for_status()
                    expected_md5 = md5_response.text.split()[0]  # Extract MD5 from response
                    
                    calculated_md5 = calculate_md5(output_path)
                    
                    if calculated_md5 == expected_md5:
                        print(f"\t\t- MD5 verification passed")
                        return True
                    else:
                        print(f"\t\t- MD5 mismatch: expected {expected_md5}, got {calculated_md5}")
                        if attempt < max_retries - 1:
                            os.remove(output_path)
                            continue
                        else:
                            return False
                            
                except Exception as e:
                    print(f"\t\t- Warning: MD5 verification failed ({e}), but file seems complete")
                    # If MD5 check fails but file size matches, accept it
                    if total_size > 0 and os.path.getsize(output_path) == total_size:
                        return True
                    elif attempt < max_retries - 1:
                        os.remove(output_path)
                        continue
                    else:
                        return False
                        
            except Exception as e:
                print(f"\t\t- Download failed: {e}")
                if attempt < max_retries - 1:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    time.sleep(5)  # Wait before retry
                    continue
                else:
                    return False
        
        return False
    
    o5m_path = os.path.join(tmp_folder_osm, f'{country}-latest.o5m')
    pbf_path = os.path.join(tmp_folder_osm, os.path.basename(url))
    
    # Check if o5m file exists and is recent (less than 1 day old)
    if os.path.exists(o5m_path) and time.time() - os.path.getmtime(o5m_path) < 86400:
        if os.path.exists(pbf_path):
            os.remove(pbf_path)  # Remove pbf if o5m exists
        return
    
    # Check if pbf file exists and is recent
    if os.path.exists(pbf_path) and time.time() - os.path.getmtime(pbf_path) < 86400:
        pass  # Use existing file
    else:
        # Remove old pbf file if it exists
        if os.path.exists(pbf_path):
            os.remove(pbf_path)
        
        # Download new file
        print("\t- Downloading OSM Data")
        start_time = time.time()
        
        success = download_with_verification(url, pbf_path, max_retries)
        
        if not success:
            raise Exception(f"Failed to download {url} after {max_retries} attempts")
        
        end_time = time.time()
        file_size_raw = utilities.sizeof_fmt(os.path.getsize(pbf_path))
        download_speed = (os.path.getsize(pbf_path) / (1024 * 1024)) / (end_time - start_time)
        print(f'\t\t- {file_size_raw} in {end_time - start_time:0.3f} s [{download_speed:0.3f} MB/s]')
    
    # Convert to o5m format
    print("\t- Converting OSM Data")
    file_size_raw = utilities.sizeof_fmt(os.path.getsize(pbf_path))
    cmdstr = f"osmconvert {pbf_path} -o={o5m_path}"
    start_time = time.time()
    os.system(cmdstr)
    file_size_conv = utilities.sizeof_fmt(os.path.getsize(o5m_path))
    print(f'\t\t- {file_size_raw} --> {file_size_conv}; {time.time() - start_time:0.3f} s')
    os.remove(pbf_path)

def length_filtering(file_path_in, file_path_out, min_length):

    print(f'\t\t- Deleting objects < {min_length} m')
    start_size = utilities.sizeof_fmt(sum(os.path.getsize(fn) for fn in sorted(glob.glob(file_path_in.replace('shp', '*')))))
    start_time = time.time()
    import geopandas as gpd
    # Read your shapefile (replace 'your_shapefile.shp' with your actual file path)
    gdf = gpd.read_file(file_path_in)

    gdf_proj = gdf.to_crs(epsg=3857)

    # Calculate the length of each line segment
    gdf_proj['line_length'] = gdf_proj['geometry'].length

    # Keep only lines longer than or equal to 50 meters
    gdf = gdf[gdf_proj['line_length'] >= min_length]

    gdf.to_file(file_path_out, driver='ESRI Shapefile')
    os.system(f'rm {file_path_in.replace("shp","*")}')
    end_size = utilities.sizeof_fmt(sum(os.path.getsize(fn) for fn in sorted(glob.glob(file_path_out.replace('shp', '*')))))
    print(f'\t\t\t- {start_size} --> {end_size}; {time.time()-start_time:0.3f} s')
