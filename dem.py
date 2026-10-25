import os
import urllib.request as urllib
from dataclasses import dataclass
from multiprocessing.dummy import Pool

import numpy as np
import polars as pl
import requests
from scipy import signal

import utilities, tunnels_bridges


# Values of track.points['dem_source']
DEM_SOURCES = ['swissALTI3D', 'ALOS AW3D30', 'EU-DEM', 'ASTER GDEM', 'SRTM', 'Copernicus DEM', 'Sonny LiDAR DEM']
DEM_SOURCE = pl.Enum(DEM_SOURCES)
SWISSALTI3D, ALOS, EUDEM, ASTER, SRTM, COPERNICUS, SONNY = range(len(DEM_SOURCES))


@dataclass(slots=True)
class Elevation:
    """Working arrays (one entry per track point) while elevations are filled in DEM by DEM."""
    lat: np.ndarray
    lon: np.ndarray
    elevation: np.ndarray  # [m a.s.l.], NaN = not yet known
    source: np.ndarray     # Index into DEM_SOURCES, -1 = none

    def missing(self) -> np.ndarray:
        return np.isnan(self.elevation)

    def cell(self, i, j) -> np.ndarray:
        """Points within the 1°x1° tile with lower left corner (lat i, lon j)."""
        return (np.floor(self.lat) == i) & (np.floor(self.lon) == j)

    def fill(self, index, values, source):
        values = np.asarray(values, dtype=float)
        values[values == -999] = np.nan  # Missing tile / no data
        self.elevation[index] = values
        self.source[index] = source


def addElevationData(parameters, assets):
    print('- Replacing Elevation Data')
    track = assets.track
    work = Elevation(lat=track['lat'], lon=track['lon'],
                     elevation=np.full(len(track), np.nan), source=np.full(len(track), -1, dtype=np.int8))

    swissAlti3DElevation(parameters, work)
    SonnyDEMElevation(parameters, work)
    CopernicusDEMElevation(parameters, work)
    ALOSGDEMElevation(parameters, work)
    EUDEMElevation(parameters, work)
    ASTERDEMElevation(parameters, work)
    SRTMDEMElevation(parameters, work)

    track.set(elevation_m=work.elevation)
    track.points = track.points.with_columns(pl.Series(
        'dem_source', [DEM_SOURCES[code] if code >= 0 else None for code in work.source], dtype=DEM_SOURCE))
    assets = tunnels_bridges.__init__(parameters, assets)
    elevation_smoothing(parameters, track)

    return assets


def lat_lon_prefix(i, j, upper=True):
    lat_prefix, lon_prefix = ('S' if i < 0 else 'N'), ('W' if j < 0 else 'E')
    return (lat_prefix, lon_prefix) if upper else (lat_prefix.lower(), lon_prefix.lower())


def unique_cells(work, index):
    """Unique tile latitudes/longitudes (floored) of the points in index."""
    return np.unique(np.floor(work.lat[index])), np.unique(np.floor(work.lon[index]))


def SonnyDEMElevation(parameters, work):
    indix_start = work.missing()

    mask = pointsinpolygon(work.lat, work.lon, f'{parameters.folder_prefix}/GeoData/DEM/SonnyDEM/SonnyDEM_Tiles_Outline_Singlepart.shp')

    lat_list, lon_list = unique_cells(work, mask & indix_start)

    if len(lat_list)>0:
        print('\t- Adding Sonny LiDAR DEM Data')

    tile_folder = f'{parameters.folder_prefix}/GeoData/DEM/SonnyDEM'

    if not os.path.exists(tile_folder):
        os.makedirs(tile_folder)

    for i in lat_list:
        for j in lon_list:
            lat_prefix, lon_prefix = lat_lon_prefix(i, j)
            tilename = f"{tile_folder}/{lat_prefix}{np.abs(i):02.0f}{lon_prefix}{np.abs(j):03.0f}.tif"

            indix = work.cell(i, j) & work.missing()
            if indix.any():
                work.fill(indix, utilities.getpixelvalues(work.lat[indix], work.lon[indix], tilename), SONNY)

    if indix_start.sum() > work.missing().sum():  # If height values have been added
        parameters.dem.append('Sonny (LiDAR DEM)')

def EUDEMElevation(parameters, work):
    mask = pointsinpolygon(work.lat, work.lon, f'{parameters.folder_prefix}/GeoData/DEM/EUDEM/EUDEM_Boundaries_Singleparts.shp')

    indix = np.flatnonzero(mask & work.missing())

    url_list = []

    tile_folder = f'{parameters.folder_prefix}/GeoData/DEM/EUDEM'

    lat_list, lon_list = unique_cells(work, indix)

    if len(lat_list) > 0:
        print('\t- Adding EUDEM Data')

    if not os.path.exists(tile_folder):
        os.makedirs(tile_folder)

    def tile_id(i, j):
        lat_prefix, lon_prefix = lat_lon_prefix(i, j, upper=False)
        return f'eudem_dem_5deg_{lat_prefix}{np.abs(np.floor(i / 5) * 5):02.0f}{lon_prefix}{np.abs(np.floor(j / 5) * 5):03.0f}'

    in_cell = lambda i, j: work.cell(i, j)[indix]

    for i in lat_list:
        for j in lon_list:
            if not os.path.exists(f'{tile_folder}/{tile_id(i, j)}.tif') and in_cell(i, j).any():
                server_url = 'https://gisco-services.ec.europa.eu/dem/5degree'
                url_list.append(f'{server_url}/{tile_id(i, j)}.ZIP')
                url_list = list(set(url_list))

    if len(url_list) > 0:
        if utilities.check_file(url_list[0], timeout=5):
            utilities.disk_housekeeping(parameters)
            numthreads = min(len(url_list), 20)

            if len(url_list) == 1:
                print(f'\t\t- Downloading {len(url_list):d} EUDEM tile (Warning: big file).')
            else:
                print(f'\t\t- Downloading {len(url_list):d} EUDEM tiles (Warning: big files)')
            Pool(numthreads).starmap(downloadEUDEMtiles,
                                     zip(url_list, [tile_folder] * len(url_list), [parameters] * len(url_list)))
        else:
            if not utilities.check_internet_connection():
                print('\t\t- No internet connection - cannot download DEM tiles')
            else:
                print('\t\t- Server not responding - cannot download DEM tiles')
            return

    for i in lat_list:
        for j in lon_list:
            if in_cell(i, j).any():
                tile_name = f'{tile_folder}/{tile_id(i, j)}.tif'

                if not os.path.exists(tile_name):
                    print(f'\t\t\t- Tile {tile_id(i, j)} not available')
                    continue

            jndix = indix[in_cell(i, j) & work.missing()[indix]]
            if len(jndix) > 0:
                work.fill(jndix, utilities.getpixelvalues(work.lat[jndix], work.lon[jndix], tile_name), EUDEM)

    if len(indix) > (mask & work.missing()).sum():  # If height values have been added
        parameters.dem.append('ESA (EU-DEM)')


def CopernicusDEMElevation(parameters, work):
    url_list = []

    tile_folder = f'{parameters.folder_prefix}/GeoData/DEM/CopernicusDEM'
    indix = np.flatnonzero(work.missing())
    lat_list, lon_list = unique_cells(work, indix)

    if len(lat_list) > 0:
        print('\t- Adding Copernicus DEM Data')

    if not os.path.exists(tile_folder):
        os.makedirs(tile_folder)

    def tile_id(i, j):
        lat_prefix, lon_prefix = lat_lon_prefix(i, j)
        return f'Copernicus_DSM_COG_10_{lat_prefix}{np.abs(np.floor(i)):02.0f}_00_{lon_prefix}{np.abs(np.floor(j)):03.0f}_00_DEM'

    in_cell = lambda i, j: work.cell(i, j)[indix]

    for i in lat_list:
        for j in lon_list:
            if not os.path.exists(f'{tile_folder}/{tile_id(i, j)}_10xInt32.tif') and in_cell(i, j).any():
                url_list.append(tile_id(i, j))
                url_list = list(set(url_list))
    if len(url_list) > 0:
        if utilities.check_internet_connection():
            utilities.disk_housekeeping(parameters)
            numthreads = min(len(url_list), 4)

            if len(url_list) == 1:
                print(f'\t\t- Downloading {len(url_list):d} Copernicus DEM tile.')
            else:
                print(f'\t\t- Downloading {len(url_list):d} Copernicus DEM tiles.')
            Pool(numthreads).starmap(downloadCopernicusDEMtiles,
                                     zip(url_list, [tile_folder] * len(url_list), [parameters] * len(url_list)))
        else:
            print('\t\t- No internet connection - cannot download DEM tiles')

            return

    for i in lat_list:
        for j in lon_list:
            if in_cell(i, j).any():
                tile_name = os.path.join(tile_folder, f'{tile_id(i, j)}_10xInt32.tif')

                if not os.path.exists(tile_name):
                    print(f'\t\t\t- Tile {os.path.basename(tile_name)} not available')
                    continue

            jndix = indix[in_cell(i, j) & work.missing()[indix]]
            if len(jndix) > 0:
                work.fill(jndix, utilities.getpixelvalues(work.lat[jndix], work.lon[jndix], tile_name) / 10, # Because Tiles stored as 10xInt32
                          COPERNICUS)

    if len(indix) > work.missing().sum():  # If height values have been added
        parameters.dem.append('ESA (Copernicus DEM)')


def ASTERDEMElevation(parameters, work):
    url_list = []

    tile_folder = f'{parameters.folder_prefix}/GeoData/DEM/ASTER'
    indix_start = work.missing()
    lat_list, lon_list = unique_cells(work, indix_start)
    if indix_start.any():
        print('\t- Adding ASTER DEM Data')

    if not os.path.exists(tile_folder):
        os.makedirs(tile_folder)

    def tile_id(i, j):
        lat_prefix, lon_prefix = lat_lon_prefix(i, j)
        return f'ASTGTMV003_{lat_prefix}{np.abs(i):02.0f}{lon_prefix}{np.abs(j):03.0f}'

    for i in lat_list:
        for j in lon_list:
            if not os.path.exists(f'{tile_folder}/{tile_id(i, j)}_dem.tif') and (work.cell(i, j) & work.missing()).any():
                server_url = 'https://e4ftl01.cr.usgs.gov/ASTT/ASTGTM.003/2000.03.01'
                url_list.append(f'{server_url}/{tile_id(i, j)}.zip')
    if len(url_list) > 0:
        if utilities.check_internet_connection():
            utilities.disk_housekeeping(parameters)
            if len(url_list) == 1:
                print(f'\t\t- Downloading {len(url_list):d} ASTER DEM tile')
            else:
                print(f'\t\t- Downloading {len(url_list):d} ASTER DEM tiles')
            for url in url_list:
                downloadASTERtiles(url, tile_folder, parameters)
        else:
            print('\t\t- No internet connection - cannot download missing DEM tiles')

    for i in lat_list:
        for j in lon_list:
            tilename = os.path.join(tile_folder, f'{tile_id(i, j)}_dem.tif')

            indix = work.cell(i, j) & work.missing()
            if indix.any():
                work.fill(indix, utilities.getpixelvalues(work.lat[indix], work.lon[indix], tilename), ASTER)

    if indix_start.sum() > work.missing().sum():  # If height values have been added
        parameters.dem.append('NASA (ASTER GDEM)')


def ALOSGDEMElevation(parameters, work):
    url_list = []

    tile_folder = f'{parameters.folder_prefix}/GeoData/DEM/ALOS'
    indix_start = work.missing()
    lat_list, lon_list = unique_cells(work, indix_start)

    if indix_start.any():
        print('\t- Adding ALOS DEM Data')

    if not os.path.exists(tile_folder):
        os.makedirs(tile_folder)

    def tile_id(i, j):
        lat_prefix, lon_prefix = lat_lon_prefix(i, j)
        return f'{lat_prefix}{np.abs(i):03.0f}{lon_prefix}{np.abs(j):03.0f}'

    for i in lat_list:
        for j in lon_list:
            if not os.path.exists(f'{tile_folder}/ALPSMLC30_{tile_id(i, j)}_DSM.tif') and (work.cell(i, j) & work.missing()).any():
                server_url = 'ftp://ftp.eorc.jaxa.jp/pub/ALOS/ext1/AW3D30/release_v2404'
                url_list.append(f'{server_url}/{tile_id(np.floor(i / 5) * 5, np.floor(j / 5) * 5)}/{tile_id(i, j)}.zip')
    if len(url_list) > 0:
        if utilities.check_file(url_list[0], timeout=5):
            utilities.disk_housekeeping(parameters)
            numthreads = min(len(url_list), 20)

            if len(url_list) == 1:
                print('\t\t- Downloading {:d} ALOS GDEM tile'.format(len(url_list)))
            else:
                print('\t\t- Downloading {:d} ALOS GDEM tiles'.format(len(url_list)))
            Pool(numthreads).starmap(downloadALOStiles,
                                     zip(url_list, [tile_folder] * len(url_list), [parameters] * len(url_list)))
        else:
            if not utilities.check_internet_connection():
                print('\t\t- No internet connection - cannot download DEM tiles')
            else:
                print('\t\t- Server not responding - cannot download DEM tiles')
            return

    for i in lat_list:
        for j in lon_list:
            tilename = f'{tile_folder}/ALPSMLC30_{tile_id(i, j)}_DSM.tif'

            indix = work.cell(i, j) & work.missing()
            if indix.any():
                work.fill(indix, utilities.getpixelvalues(work.lat[indix], work.lon[indix], tilename), ALOS)

    if indix_start.sum() > work.missing().sum():  # If height values have been added
        parameters.dem.append('JAXA (AW3D30)')


def SRTMDEMElevation(parameters, work):
    import srtm

    tilefolder = f'{parameters.folder_prefix}/GeoData/DEM/SRTM3'
    if not os.path.exists(tilefolder):
        os.makedirs(tilefolder)

    indix = np.flatnonzero(work.missing())
    if len(indix) == 0:
        return
    print('\t- Adding SRTM DEM Data')

    elevation_data = srtm.get_data(local_cache_dir=tilefolder)

    values = [elevation_data.get_elevation(work.lat[i], work.lon[i]) for i in indix]
    work.fill(indix, [np.nan if value is None else value for value in values], SRTM)

    if len(indix) > work.missing().sum():  # If height values have been added
        parameters.dem.append('NASA (SRTM)')


def downloadALOStiles(url, save_folder, parameters):
    import shutil

    savefilename = url.split("/")[-1]
    if not os.path.exists(parameters.temp_folder):
        try:
            os.makedirs(parameters.temp_folder)
        except FileExistsError:
            pass
    try:
        urllib.urlretrieve(url, f'{parameters.temp_folder}/{savefilename}')  # Download the tile
        utilities.extract_specific_files(f'{parameters.temp_folder}/{savefilename}', save_folder, '_DSM.tif')
        os.remove(os.path.join(parameters.temp_folder, savefilename))  # Remove zip file
        shutil.rmtree(os.path.join(parameters.temp_folder, save_folder,
                                   savefilename.replace(".zip", "")))  # Remove extracted containing folder
        return
    except:
        return


def downloadASTERtiles(url, save_folder, parameters):
    save_filename = url.split("/")[-1]
    if not os.path.exists(parameters.temp_folder):
        try:
            os.makedirs(parameters.temp_folder)
        except FileExistsError:
            pass
    try:
        username, password = utilities.get_credentials('USGS')
        session = SessionWithHeaderRedirection(username, password)

        try:
            # submit the request using the session
            response = session.get(url, stream=True)
            # raise an exception in case of http errors
            response.raise_for_status()

            # save the file
            with open(os.path.join(parameters.temp_folder, save_filename), 'wb') as fd:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    fd.write(chunk)

        except requests.exceptions.HTTPError as e:
            # handle any errors here
            print(e)

        utilities.extract_specific_files(os.path.join(parameters.temp_folder, save_filename), save_folder, '_dem.tif')
        os.remove("{}/{}".format(parameters.temp_folder, save_filename))
        return
    except:
        return


def downloadEUDEMtiles(url, save_folder, parameters):
    save_filename = url.split("/")[-1]
    if not os.path.exists(parameters.temp_folder):
        try:
            os.makedirs(parameters.temp_folder)
        except FileExistsError:
            pass
    try:
        urllib.urlretrieve(url, os.path.join(parameters.temp_folder, save_filename))
        utilities.extract_specific_files(os.path.join(parameters.temp_folder, save_filename), save_folder, '.tif')
        os.remove(os.path.join(parameters.temp_folder, save_filename))
        return
    except:
        return


def downloadCopernicusDEMtiles(tile_name, save_folder, parameters):
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config

    s3 = boto3.client('s3', config=Config(signature_version=UNSIGNED))

    save_filename = f'{tile_name}.tif'
    if not os.path.exists(parameters.temp_folder):
        try:
            os.makedirs(parameters.temp_folder)
        except FileExistsError:
            pass
    try:
        s3.download_file('copernicus-dem-30m',
                         f'{tile_name}/{save_filename}',
                         f'{os.path.join(parameters.temp_folder, save_filename)}', )
        os.system(f'gdal_calc.py --quiet -A {os.path.join(parameters.temp_folder,save_filename)} --A_band=1 --outfile={os.path.join(save_folder, save_filename.replace(".tif", "_10xInt32.tif"))} --calc="round(A*10)" --NoDataValue -9990 --type Int32 --co COMPRESS=DEFLATE --co PREDICTOR=2 --overwrite')
        os.remove(os.path.join(parameters.temp_folder,save_filename))
        return
    except:
        return


def wgs84_to_lv95(lat, lon):
    """
    WGS84 [°] -> Swiss LV95 (E, N) [m], vectorized
    (approximate swisstopo formulas, ~1 m accuracy).
    """
    def dec_to_sex(angle):  # Decimal degrees -> sexagesimal seconds
        deg = np.trunc(angle)
        mnt = np.trunc((angle - deg) * 60)
        sec = (((angle - deg) * 60) - mnt) * 60
        return sec + mnt * 60. + deg * 3600.

    # Auxiliary values (% Bern)
    lat_aux = (dec_to_sex(lat) - 169028.66) / 10000
    lng_aux = (dec_to_sex(lon) - 26782.5) / 10000
    east = (600072.37
            + 211455.93 * lng_aux
            - 10938.51 * lng_aux * lat_aux
            - 0.36 * lng_aux * lat_aux ** 2
            - 44.54 * lng_aux ** 3)
    north = (200147.07
             + 308807.95 * lat_aux
             + 3745.25 * lng_aux ** 2
             + 76.63 * lat_aux ** 2
             - 194.56 * lng_aux ** 2 * lat_aux
             + 119.79 * lat_aux ** 3)
    return east + 2000000, north + 1000000


def swissAlti3DElevation(parameters, work):
    points_x, points_y = wgs84_to_lv95(work.lat, work.lon)

    mask = pointsinpolygon(points_y, points_x, f'{parameters.folder_prefix}/GeoData/DEM/swisstopo/swissBOUNDARIES3D_1_3_TLM_LANDESGEBIET_Simplified.shp')
    if mask.any():
        parameters.dem.append('swisstopo (swissALTI3D)')
        print('\t- Adding swissALTI3D DEM Data')

        tilename = f'{parameters.folder_prefix}/GeoData/DEM/swisstopo/swissALTI3D_5m_elevation_r2023_10xuint16_compressed_DEFLATE.tif'  # Faster
        work.fill(mask, utilities.getpixelvalues(points_y[mask], points_x[mask], tilename) / 10, SWISSALTI3D)


def pointsinpolygon(point_lat, point_lon, shapefile_path):
    """Boolean mask of the points lying within any polygon of the shapefile (all False if it cannot be read)."""
    try:
        polygons = utilities.read_shapes(shapefile_path)
    except Exception:
        return np.zeros(len(point_lat), dtype=bool)
    return utilities.points_in_shapes(point_lat, point_lon, polygons)

class SessionWithHeaderRedirection(requests.Session):
    AUTH_HOST = 'urs.earthdata.nasa.gov'

    def __init__(self, username, password):
        super().__init__()
        self.auth = (username, password)

        # Overrides from the library to keep headers when redirected to or from
        # the NASA auth host.

    def rebuild_auth(self, prepared_request, response):
        headers = prepared_request.headers
        url = prepared_request.url

        if 'Authorization' in headers:
            original_parsed = requests.utils.urlparse(response.request.url)
            redirect_parsed = requests.utils.urlparse(url)
            if (
                    original_parsed.hostname != redirect_parsed.hostname) and redirect_parsed.hostname != self.AUTH_HOST and original_parsed.hostname != self.AUTH_HOST:
                del headers['Authorization']

        return

def elevation_smoothing(parameters, track):
    elevation, distance = track['elevation_m'], track['distance_m']
    if len(parameters.dem)==1 and 'swisstopo' in parameters.dem[0]:
        for idx in range(5):
            elevation = signal.savgol_filter(elevation, 7, 2)
    elif len(parameters.dem)>1 and 'swisstopo' in parameters.dem[0]:
        in_ch = (track.points['dem_source'] == 'swissALTI3D').fill_null(False).to_numpy()
        elev_ex_ch = utilities.lowess_smoothing(distance, elevation)
        elev_in_ch = elevation
        for idx in range(5):
            elev_in_ch = signal.savgol_filter(elev_in_ch, 7, 2)
        elevation = np.where(in_ch, elev_in_ch, elev_ex_ch)
    else:
        elevation = utilities.lowess_smoothing(distance, elevation)
    track.set(elevation_m=elevation)
