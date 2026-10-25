from zipfile import ZipFile
import csv
import glob
import os
import sys
import time
import pwd
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

def atomic_directory_swap(staging_dir, production_symlink):
    """Atomically swap staging directory to production using symlinks"""
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    
    # e.g. .../tunnels_staging -> .../tunnels_v20260119-184926
    base_name = os.path.basename(production_symlink)
    parent_dir = os.path.dirname(staging_dir)
    new_version_dir = os.path.join(parent_dir, f"{base_name}_v{timestamp}")
    os.rename(staging_dir, new_version_dir)

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

        osm.outlines_from_kml(csv_path)

        # Use staging directories that don't interfere with production
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
            
            # Run multi2single.py - no sudo needed if permissions are correct
            cmd = f'{sys.executable} {osm.MULTI2SINGLE} {file} {file.replace(".shp", "_MultiPoly.shp")}'
            result = os.system(f'{cmd} > /dev/null 2>&1')
            
            if result != 0:
                print(f' FAILED!')
                print(f'  Command: {cmd}')
                print(f'  Return code: {result}')
                if os.geteuid() != 0:
                    print(f'  Hint: If permission denied, run fix_ownership_once.sh as root first')
                raise Exception(f'multi2single.py failed with code {result}')
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

        # Write data date into the tunnels staging folder (must happen AFTER the
        # non-MultiPoly cleanup above). It is swapped atomically together with the data
        # and read by the Streamlit footer via utilities.get_osm_data_date().
        osm_date_file = os.path.join(staging_tunnels, 'osm_data_date.json')
        with open(osm_date_file, 'w') as f:
            json.dump({'osm_data_date': time.strftime('%Y-%m'),
                       'updated': time.strftime('%Y-%m-%dT%H:%M:%S')}, f, indent=4)
        fix_ownership(osm_date_file, target_user)

        # Archive the current production files
        print('Archiving old production files...')
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        
        # Archive tunnels if production exists
        if os.path.exists(production_tunnels) or os.path.islink(production_tunnels):
            archive_dir = os.path.join(base_path, 'archive', 'tunnels')
            os.makedirs(archive_dir, exist_ok=True)
            
            # Resolve symlink to actual directory
            actual_tunnels = os.path.realpath(production_tunnels) if os.path.islink(production_tunnels) else production_tunnels
            
            if os.path.exists(actual_tunnels):
                with ZipFile(os.path.join(archive_dir, f'{timestamp}.zip'), 'w') as zipObj:
                    for filename in glob.glob(os.path.join(actual_tunnels, '*_MultiPoly.*')):
                        zipObj.write(filename, os.path.basename(filename))
                
                # Clean up old archives (keep last 3)
                os.system(f'ls {archive_dir}/*.zip -1t | tail -n +4 | xargs rm -f')
        
        # Archive bridges if production exists
        if os.path.exists(production_bridges) or os.path.islink(production_bridges):
            archive_dir = os.path.join(base_path, 'archive', 'bridges')
            os.makedirs(archive_dir, exist_ok=True)
            
            # Resolve symlink to actual directory
            actual_bridges = os.path.realpath(production_bridges) if os.path.islink(production_bridges) else production_bridges
            
            if os.path.exists(actual_bridges):
                with ZipFile(os.path.join(archive_dir, f'{timestamp}.zip'), 'w') as zipObj:
                    for filename in glob.glob(os.path.join(actual_bridges, '*_MultiPoly.*')):
                        zipObj.write(filename, os.path.basename(filename))
                
                # Clean up old archives (keep last 3)
                os.system(f'ls {archive_dir}/*.zip -1t | tail -n +4 | xargs rm -f')

        # ATOMIC SWAP: Point production symlinks to new staging directories
        print('Swapping to new version (atomic operation)...')
        
        # For tunnels
        new_tunnels_version = atomic_directory_swap(staging_tunnels, production_tunnels)
        print(f'Tunnels now pointing to: {new_tunnels_version}')
        fix_ownership(new_tunnels_version, target_user)
        fix_ownership(production_tunnels, target_user)
        
        # For bridges
        new_bridges_version = atomic_directory_swap(staging_bridges, production_bridges)
        print(f'Bridges now pointing to: {new_bridges_version}')
        fix_ownership(new_bridges_version, target_user)
        fix_ownership(production_bridges, target_user)
        
        # Clean up old versioned directories (keep last 2 versions)
        print('Cleaning up old versions...')
        for pattern in ['tunnels_v*', 'bridges_v*']:
            old_versions = sorted(glob.glob(os.path.join(savepath, pattern)))
            if len(old_versions) > 2:
                for old_dir in old_versions[:-2]:
                    print(f'Removing old version: {old_dir}')
                    os.system(f'rm -rf {old_dir}')

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
