from zipfile import ZipFile
import csv
import glob
import os
import sys
import time
import pwd
import shutil
import fcntl
from pathlib import Path
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))  # Project root: config, utilities
import config
import geofabrik_update
import osm
import utilities

def get_target_user(base_path):
    """Determine the target user for file ownership"""
    # If running as root, the files keep the owner of the OSM data folder
    if os.geteuid() == 0:
        return pwd.getpwuid(os.stat(base_path).st_uid).pw_name
    else:
        return pwd.getpwuid(os.geteuid()).pw_name

def fix_ownership(path, target_user):
    """Fix ownership of files/directories"""
    if os.geteuid() != 0:
        # Not running as root, can't change ownership
        return
    
    try:
        uid = pwd.getpwnam(target_user).pw_uid
        gid = pwd.getpwnam(target_user).pw_gid
        
        if os.path.isfile(path):
            os.chown(path, uid, gid)
        elif os.path.isdir(path):
            # Recursively fix ownership
            for root, dirs, files in os.walk(path):
                os.chown(root, uid, gid)
                for d in dirs:
                    os.chown(os.path.join(root, d), uid, gid)
                for f in files:
                    os.chown(os.path.join(root, f), uid, gid)
    except Exception as e:
        print(f'Warning: Could not fix ownership of {path}: {e}')

def acquire_update_lock(lock_file, target_user):
    """Acquire exclusive lock to prevent concurrent updates and signal ongoing update"""
    lock_fd = open(lock_file, 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(f"{os.getpid()}\n{time.time()}")
        lock_fd.flush()
        
        # Fix ownership of lock file if running as root
        fix_ownership(lock_file, target_user)
        
        return lock_fd
    except IOError:
        lock_fd.close()
        return None

def release_update_lock(lock_fd):
    """Release the update lock"""
    if lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()

KEEP_ARCHIVES = 3  # Zip archives per data set in GeoData/OSM/archive/<name>/
KEEP_VERSIONS = 2  # Versioned folders per data set (<name>_v<timestamp>)

def archive_production(production, archive_dir, timestamp, target_user):
    """Zip the production *_MultiPoly.* files into archive_dir/<timestamp>.zip; keep the newest KEEP_ARCHIVES."""
    actual = os.path.realpath(production)
    files = sorted(glob.glob(os.path.join(actual, '*_MultiPoly.*')))
    os.makedirs(archive_dir, exist_ok=True)
    legacy = os.path.join(actual, 'old')  # Former outline archives inside the outlines folder
    for old_zip in sorted(glob.glob(os.path.join(legacy, '*.zip'))):
        shutil.move(old_zip, os.path.join(archive_dir, os.path.basename(old_zip)))
    if files:
        with ZipFile(os.path.join(archive_dir, f'{timestamp}.zip'), 'w') as archive:
            for filename in files:
                archive.write(filename, os.path.basename(filename))
    for old_zip in sorted(glob.glob(os.path.join(archive_dir, '*.zip')))[:-KEEP_ARCHIVES]:  # Names sort by time
        os.remove(old_zip)
    fix_ownership(archive_dir, target_user)

def atomic_directory_swap(staging_dir, production_symlink):
    """Atomically swap staging directory to production using symlinks"""
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    
    # e.g. .../tunnels_staging -> .../tunnels_v20260119-184926
    base_name = os.path.basename(production_symlink)
    parent_dir = os.path.dirname(staging_dir)
    new_version_dir = os.path.join(parent_dir, f"{base_name}_v{timestamp}")
    os.rename(staging_dir, new_version_dir)

    # A plain folder in place of the symlink (e.g. unpacked from the published data) becomes a version of its own
    if os.path.isdir(production_symlink) and not os.path.islink(production_symlink):
        os.rename(production_symlink, f"{production_symlink}_v{time.strftime('%Y%m%d-%H%M%S', time.localtime(os.path.getmtime(production_symlink)))}")

    # Relative symlink, created under a temporary name
    temp_symlink = f"{production_symlink}_tmp_{timestamp}"
    os.symlink(os.path.basename(new_version_dir), temp_symlink)

    # Atomically replace symlink (this is atomic on Linux)
    os.rename(temp_symlink, production_symlink)
    
    return new_version_dir

try:
    if config.DATA_DIR is None:
        sys.exit('Set data_dir in config.toml (or AERORACLE_DATA_DIR)')
    folderPrefix = config.DATA_DIR
    tmp_folder = osm.TMP_DIR

    # Setup paths
    base_path = os.path.join(folderPrefix, 'GeoData/OSM')
    csv_path = os.path.join(base_path, 'osm_all.csv')
    
    # Determine target user for file ownership
    target_user = get_target_user(base_path)
    if os.geteuid() == 0:
        print(f'Running as root. Files will be owned by: {target_user}')
    
    # Lock file to prevent concurrent updates
    update_lock_file = os.path.join(base_path, '.update_in_progress.lock')
    
    # Try to acquire lock
    print('Attempting to acquire update lock...')
    lock_fd = acquire_update_lock(update_lock_file, target_user)
    if lock_fd is None:
        print('Another update is already in progress. Exiting.')
        sys.exit(1)
    
    print('Lock acquired. Starting update process.')
    
    try:
        print('Fetching List of assets on download.geofabrik.de')
        geofabrik_update.create_csv(csv_path=csv_path, buffer_distance_meters=30)
        regions = sys.argv[1:]  # Optional: only these regions (CSV column name), e.g. for testing with AERORACLE_DATA_DIR elsewhere
        if regions:
            with open(csv_path) as f:
                lines = f.readlines()
            with open(csv_path, 'w') as f:
                f.writelines([lines[0]] + [line for line in lines[1:] if line.split(';')[1] in regions])
            print(f'Only regions: {", ".join(regions)}')

        # Use staging directories that don't interfere with production
        staging_outlines = os.path.join(base_path, 'outlines_staging')
        production_outlines = os.path.join(base_path, 'outlines')
        print('Building outlines (staging)...')
        osm.outlines_from_kml(csv_path, staging_outlines)

        savepath = os.path.join(base_path, 'countries')
        staging_tunnels = os.path.join(savepath, 'tunnels_staging')
        staging_bridges = os.path.join(savepath, 'bridges_staging')
        
        # Production symlinks (these will always point to valid data)
        production_tunnels = os.path.join(savepath, 'tunnels')
        production_bridges = os.path.join(savepath, 'bridges')
        
        # Create staging directories
        os.makedirs(staging_tunnels, exist_ok=True)
        os.makedirs(staging_bridges, exist_ok=True)

        # Process all countries - extract both tunnels AND bridges in single pass
        print('Processing countries (extracting tunnels and bridges)...')
        with open(csv_path) as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=';')
            next(csv_reader)  # Skipping header line
            for row in csv_reader:
                print('Working on {}'.format(row[0]))
                osm.download_extract_buffer(row[0], row[1], row[2], 'tunnels', staging_tunnels)
                osm.download_extract_buffer(row[0], row[1], row[2], 'bridges', staging_bridges)
                try:
                    os.remove(f"{os.path.join(tmp_folder, 'RAW', os.path.basename(row[2]).replace('.osm.pbf', '.o5m'))}")
                except:
                    pass

        print('Merging SHPs (Tunnels)')
        osm.merge_shapefiles(staging_tunnels)
        
        print('Merging SHPs (Bridges)')
        osm.merge_shapefiles(staging_bridges)

        # Clean up temporary files
        os.system('rm -rf {}/SHP'.format(tmp_folder))
        os.system(f'rm -rf {os.path.join(tmp_folder,"OSM/RAW")}')
        os.system(f'rm -rf {os.path.join(tmp_folder, "SHP", "tunnels")}')
        os.system(f'rm -rf {os.path.join(tmp_folder, "SHP", "bridges")}')

        # Multi2single conversion
        merged_files = sorted(glob.glob(os.path.join(savepath, '*_staging/*_merged.shp')))
        for file in merged_files:
            print(f'Multipart -> Singleparts ({os.path.basename(file).replace("_merged.shp", "").capitalize()}', end="")
            start_time = time.time()
            
            result = osm.multi_to_single(file, file.replace(".shp", "_MultiPoly.shp"))
            
            if result != 0:
                print(' FAILED!')
                raise Exception(f'ogr2ogr (multipart -> singleparts) failed with code {result}')
            else:
                output_size = os.path.getsize(file.replace(".shp", "_MultiPoly.shp"))
                print(f'; {utilities.sizeof_fmt(os.path.getsize(file))} --> {utilities.sizeof_fmt(output_size)}; {time.time()-start_time:0.3f} s)')
                
                # Fix ownership of created file if running as root
                if os.geteuid() == 0:
                    fix_ownership(file.replace(".shp", "_MultiPoly.shp"), target_user)
                    # Fix all related files (.shx, .dbf, etc)
                    base_name = file.replace("_merged.shp", "_MultiPoly")
                    for ext in ['.shp', '.shx', '.dbf', '.prj']:
                        if os.path.exists(base_name + ext):
                            fix_ownership(base_name + ext, target_user)

        # Clean up non-MultiPoly files
        os.system(f'rm {os.path.join(savepath,"*_staging","*.dbf")}')
        for file in sorted(glob.glob(os.path.join(savepath, '*_staging', '*'))):
            if "MultiPoly" not in os.path.basename(file) and not os.path.isdir(file):
                os.remove(file)

        # Favourites handling
        favourites = ['switzerland', 'liechtenstein', 'germany', 'france', 'italy', 'austria', 'spain', 'canaryislands']
        fav_counter = 0
        all_files = sorted(glob.glob(os.path.join(savepath, '*_staging', '*')))
        for fav in favourites:
            fav_counter += 1
            for file in all_files:
                if fav in file:
                    os.rename(file, file.replace(fav, f'{fav_counter}_{fav}'))

        # Never swap in (nearly) empty data: compare with the current production version
        data_sets = (('outlines', staging_outlines, production_outlines),
                     ('tunnels', staging_tunnels, production_tunnels), ('bridges', staging_bridges, production_bridges))
        for _, staging, production in data_sets:
            new_count = len(glob.glob(os.path.join(staging, '*_MultiPoly.shp')))
            old_count = len(glob.glob(os.path.join(production, '*_MultiPoly.shp')))
            if new_count == 0 or new_count < 0.8 * old_count:
                raise Exception(f'{os.path.basename(production).capitalize()}: only {new_count} new files (currently {old_count}). '
                                f'Production data kept; check the output above for failed steps.')

        # Write data date into the tunnels staging folder (must happen AFTER the
        # non-MultiPoly cleanup above). It is swapped atomically together with the data
        # and read by the Streamlit footer via utilities.get_osm_data_date().
        osm_date_file = os.path.join(staging_tunnels, 'osm_data_date.json')
        with open(osm_date_file, 'w') as f:
            json.dump({'osm_data_date': time.strftime('%Y-%m'),
                       'updated': time.strftime('%Y-%m-%dT%H:%M:%S')}, f, indent=4)
        fix_ownership(osm_date_file, target_user)

        # Archive the current production files (GeoData/OSM/archive/<name>/, read by the app's restore)
        print('Archiving old production files...')
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        for name, _, production in data_sets:
            archive_production(production, os.path.join(base_path, 'archive', name), timestamp, target_user)

        # ATOMIC SWAP: Point production symlinks to new staging directories
        print('Swapping to new version (atomic operation)...')
        for name, staging, production in data_sets:
            new_version = atomic_directory_swap(staging, production)
            print(f'{name.capitalize()} now pointing to: {new_version}')
            fix_ownership(new_version, target_user)
            fix_ownership(production, target_user)

        # Clean up old versioned directories (keep last 2 versions)
        print('Cleaning up old versions...')
        for name, _, production in data_sets:
            for old_dir in sorted(glob.glob(f'{production}_v*'))[:-KEEP_VERSIONS]:
                print(f'Removing old version: {old_dir}')
                shutil.rmtree(old_dir)

        os.system('rm -rf {}'.format(tmp_folder))
        os.makedirs(config.INPUT_DIR, exist_ok=True)
        
        print('Update completed successfully!')
        print(f'All files owned by: {target_user}')
        
    finally:
        # Always release the lock
        release_update_lock(lock_fd)
        # Remove lock file
        try:
            os.remove(update_lock_file)
        except:
            pass
        
except KeyboardInterrupt:
    print('Manually interrupted')
finally:
    os.system('rm -f *.osm.pbf*.tmp')
    os.system('rm -f osmfilter_tempfile*')
