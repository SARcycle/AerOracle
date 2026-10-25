import os

import numpy as np

import dem  # Needed for parallelizing the data ingestion and preprocessing steps
import utilities

class Assets:
    def __init__(self, parameters):
        #with concurrent.futures.ThreadPoolExecutor() as executor:
            #task1 = executor.submit(self.load_tunnels, parameters)
            #task2 = executor.submit(self.data_preprocessing, parameters)

        #self.__setattr__('tunnels', task1.result())
        #self.__setattr__('track', task2.result())
        self.__setattr__('track', self.data_preprocessing(parameters).track)
        self.track.set(gravity_ms2=self.get_gravity(parameters))


    def load_tunnels(self, parameters):
        import fiona
        import shapely.geometry
        from shapely.geometry import shape
        from shapely.prepared import prep

        try:
            tunnels = prep(shapely.geometry.MultiPolygon([shape(pol['geometry']) for pol in fiona.open(
                f'{parameters.folder_prefix}/GeoData/OSM/Tunnels/Tunnels_Europe_50mBuffer_Dissolve_MultiPoly.shp')]))
            self.__setattr__('tunnels', tunnels)
            return tunnels
        except:
            print('- Restoring European Tunnels from archive')
            os.system(
                f'ls {parameters.folder_prefix}/GeoData/OSM/Tunnels/old/*.zip -1t | head -n +1 | xargs unzip -q -d / -o')
            tunnels = prep(shapely.geometry.MultiPolygon([shape(pol['geometry']) for pol in fiona.open(
                f'{parameters.folder_prefix}/GeoData/OSM/Tunnels/Tunnels_Europe_50mBuffer_Dissolve_MultiPoly.shp')]))
            self.__setattr__('tunnels', tunnels)
            return tunnels

    def data_preprocessing(self, parameters):
        import track
        if not parameters.url is None: # if URL is given
            # utilities.tcx_download(parameters)
            utilities.rwgps_download(parameters)
        self.track = track.Track(parameters)
        rwgps_json = getattr(parameters, 'rwgps_json', None)
        if rwgps_json and os.path.exists(rwgps_json):
            os.remove(rwgps_json)        

        return dem.addElevationData(parameters, self)

    def get_gravity(self, parameters):
        print('- Calculating Gravity')
        lat, elevation = self.track['lat'], self.track['elevation_m']
        # Gravity data from https://bgi.obs-mip.fr/catalogue/?uuid=df2dab2d-a826-4776-b49f-61e8b284c409
        anomaly_mgal = utilities.getpixelvalues(lat, self.track['lon'], f'{parameters.folder_prefix}/GeoData/Gravimetry/WGM2012_Freeair_ponc_2min_mgal_Int16_LZW.tif')
        anomaly_mgal[anomaly_mgal == -999] = 0  # Gravity model not available: normal gravity only
        gravity_freeair_anomaly = anomaly_mgal * 1e-5
        # Calculation based on WELMEC-Formula (https://de.wikipedia.org/wiki/Normalschwereformel#WELMEC-Formel)
        gravity = gravity_freeair_anomaly + ((1+0.0053024*np.power(np.sin(np.deg2rad(lat)), 2)-0.0000058*np.power(np.sin(np.deg2rad(2*lat)), 2))*9.780318-0.000003085*elevation)
        return gravity
