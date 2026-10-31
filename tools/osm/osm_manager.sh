#!/bin/bash
# OSM Data Management Helper Script
# Usage: ./osm_manager.sh [command]

set -e

# Configuration
BASE_PATH="${AERORACLE_DATA_DIR:-/data}/GeoData/OSM"  # Folder containing GeoData/, as data_dir in config.toml
COUNTRIES_PATH="$BASE_PATH/countries"
ARCHIVE_PATH="$BASE_PATH/archive"
LOCK_FILE="$BASE_PATH/.update_in_progress.lock"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
print_header() {
    echo -e "${BLUE}==================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}==================================================${NC}"
}

print_success() {
    echo -e "${GREEN}OK: $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}Warning: $1${NC}"
}

print_error() {
    echo -e "${RED}Error: $1${NC}"
}

# Point a symlink to a version folder (basename) atomically: new link under a temporary name, renamed over the old one
swap_link() {
    ln -sfn "$1" "$2.tmp_$$"
    mv -Tf "$2.tmp_$$" "$2"
}

# Extract a zip flat into a folder (Python: unzip is not always installed; older archives contain full paths)
extract_flat() {
    python3 -c 'import os, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    for m in z.infolist():
        if not m.is_dir():
            open(os.path.join(sys.argv[2], os.path.basename(m.filename)), "wb").write(z.read(m))' "$1" "$2"
}

# Command: status
cmd_status() {
    print_header "System Status"
    
    # Check if update is running
    if [ -f "$LOCK_FILE" ]; then
        print_warning "Update in progress"
        echo "Lock file: $LOCK_FILE"
        if [ -r "$LOCK_FILE" ]; then
            echo "PID: $(head -1 $LOCK_FILE)"
            LOCK_TIME=$(tail -1 $LOCK_FILE)
            CURRENT_TIME=$(date +%s)
            AGE=$((CURRENT_TIME - ${LOCK_TIME%.*}))
            echo "Age: $((AGE / 3600)) hours $((AGE % 3600 / 60)) minutes"
            
            if [ $AGE -gt 86400 ]; then
                print_warning "Lock is stale (>24 hours old)"
            fi
        fi
    else
        print_success "No update in progress"
    fi
    
    echo ""
    echo "Current Versions:"

    # Outlines (in $BASE_PATH; a plain folder until the first update that versions them)
    if [ -L "$BASE_PATH/outlines" ]; then
        OUTLINE_TARGET=$(readlink "$BASE_PATH/outlines")
        echo -e "  Outlines: ${GREEN}$OUTLINE_TARGET${NC}"

        if [ -d "$BASE_PATH/$OUTLINE_TARGET" ]; then
            FILE_COUNT=$(find "$BASE_PATH/$OUTLINE_TARGET" -name "*_MultiPoly.shp" | wc -l)
            TOTAL_SIZE=$(du -sh "$BASE_PATH/$OUTLINE_TARGET" | cut -f1)
            echo "    Files: $FILE_COUNT shapefiles"
            echo "    Size: $TOTAL_SIZE"
        fi
    elif [ -d "$BASE_PATH/outlines" ]; then
        echo "  Outlines: plain folder (versioned at the next update)"
    else
        print_error "Outlines missing"
    fi

    # Tunnels
    if [ -L "$COUNTRIES_PATH/tunnels" ]; then
        TUNNEL_TARGET=$(readlink "$COUNTRIES_PATH/tunnels")
        echo -e "  Tunnels: ${GREEN}$TUNNEL_TARGET${NC}"
        
        if [ -d "$COUNTRIES_PATH/$TUNNEL_TARGET" ]; then
            FILE_COUNT=$(find "$COUNTRIES_PATH/$TUNNEL_TARGET" -name "*_MultiPoly.shp" | wc -l)
            TOTAL_SIZE=$(du -sh "$COUNTRIES_PATH/$TUNNEL_TARGET" | cut -f1)
            echo "    Files: $FILE_COUNT shapefiles"
            echo "    Size: $TOTAL_SIZE"
        fi
    else
        print_error "Tunnels symlink missing"
    fi
    
    # Bridges
    if [ -L "$COUNTRIES_PATH/bridges" ]; then
        BRIDGE_TARGET=$(readlink "$COUNTRIES_PATH/bridges")
        echo -e "  Bridges: ${GREEN}$BRIDGE_TARGET${NC}"
        
        if [ -d "$COUNTRIES_PATH/$BRIDGE_TARGET" ]; then
            FILE_COUNT=$(find "$COUNTRIES_PATH/$BRIDGE_TARGET" -name "*_MultiPoly.shp" | wc -l)
            TOTAL_SIZE=$(du -sh "$COUNTRIES_PATH/$BRIDGE_TARGET" | cut -f1)
            echo "    Files: $FILE_COUNT shapefiles"
            echo "    Size: $TOTAL_SIZE"
        fi
    else
        print_error "Bridges symlink missing"
    fi
    
    echo ""
    echo "Available Versions:"
    for dir in $(ls -dt "$COUNTRIES_PATH"/*_v* 2>/dev/null | head -5); do
        DIRNAME=$(basename "$dir")
        SIZE=$(du -sh "$dir" | cut -f1)
        if [ -L "$COUNTRIES_PATH/tunnels" ] && [ "$(readlink "$COUNTRIES_PATH/tunnels")" = "$DIRNAME" ]; then
            echo -e "  ${GREEN}-> $DIRNAME${NC} ($SIZE) [CURRENT]"
        elif [ -L "$COUNTRIES_PATH/bridges" ] && [ "$(readlink "$COUNTRIES_PATH/bridges")" = "$DIRNAME" ]; then
            echo -e "  ${GREEN}-> $DIRNAME${NC} ($SIZE) [CURRENT]"
        else
            echo "    $DIRNAME ($SIZE)"
        fi
    done
    for dir in $(ls -dt "$BASE_PATH"/outlines_v* 2>/dev/null | head -3); do
        DIRNAME=$(basename "$dir")
        SIZE=$(du -sh "$dir" | cut -f1)
        if [ -L "$BASE_PATH/outlines" ] && [ "$(readlink "$BASE_PATH/outlines")" = "$DIRNAME" ]; then
            echo -e "  ${GREEN}-> $DIRNAME${NC} ($SIZE) [CURRENT]"
        else
            echo "    $DIRNAME ($SIZE)"
        fi
    done

    echo ""
    echo "Archives:"
    echo "  Outlines:"
    ls -lht "$ARCHIVE_PATH/outlines"/*.zip 2>/dev/null | head -3 | while read -r line; do
        echo "    $(echo $line | awk '{print $9, "("$5")"}')"
    done

    echo "  Tunnels:"
    ls -lht "$ARCHIVE_PATH/tunnels"/*.zip 2>/dev/null | head -3 | while read -r line; do
        echo "    $(echo $line | awk '{print $9, "("$5")"}')"
    done
    
    echo "  Bridges:"
    ls -lht "$ARCHIVE_PATH/bridges"/*.zip 2>/dev/null | head -3 | while read -r line; do
        echo "    $(echo $line | awk '{print $9, "("$5")"}')"
    done
    
    echo ""
    echo "Disk Usage:"
    echo "  Total: $(du -sh $COUNTRIES_PATH | cut -f1)"
    echo "  Archives: $(du -sh $ARCHIVE_PATH | cut -f1)"
}

# Command: list-versions
cmd_list_versions() {
    print_header "All Available Versions"
    
    for dir in $(ls -dt "$COUNTRIES_PATH"/*_v* "$BASE_PATH"/outlines_v* 2>/dev/null); do
        DIRNAME=$(basename "$dir")
        SIZE=$(du -sh "$dir" | cut -f1)
        FILE_COUNT=$(find "$dir" -name "*_MultiPoly.shp" 2>/dev/null | wc -l)
        MTIME=$(stat -c %y "$dir" | cut -d' ' -f1)

        if [ -L "$COUNTRIES_PATH/tunnels" ] && [ "$(readlink "$COUNTRIES_PATH/tunnels")" = "$DIRNAME" ]; then
            echo -e "${GREEN}-> $DIRNAME${NC}"
        elif [ -L "$COUNTRIES_PATH/bridges" ] && [ "$(readlink "$COUNTRIES_PATH/bridges")" = "$DIRNAME" ]; then
            echo -e "${GREEN}-> $DIRNAME${NC}"
        elif [ -L "$BASE_PATH/outlines" ] && [ "$(readlink "$BASE_PATH/outlines")" = "$DIRNAME" ]; then
            echo -e "${GREEN}-> $DIRNAME${NC}"
        else
            echo "  $DIRNAME"
        fi
        echo "    Created: $MTIME | Size: $SIZE | Files: $FILE_COUNT"
    done
}

# Command: rollback
cmd_rollback() {
    if [ -f "$LOCK_FILE" ]; then
        print_error "Cannot rollback: Update in progress"
        exit 1
    fi
    
    print_header "Rollback to Previous Version"
    
    CURRENT_TUNNEL=$(readlink "$COUNTRIES_PATH/tunnels" 2>/dev/null || echo "")
    CURRENT_BRIDGE=$(readlink "$COUNTRIES_PATH/bridges" 2>/dev/null || echo "")
    
    PREV_TUNNEL=$(ls -dt "$COUNTRIES_PATH"/tunnels_v* 2>/dev/null | grep -v "$CURRENT_TUNNEL" | head -1)
    PREV_BRIDGE=$(ls -dt "$COUNTRIES_PATH"/bridges_v* 2>/dev/null | grep -v "$CURRENT_BRIDGE" | head -1)
    CURRENT_OUTLINE=$(readlink "$BASE_PATH/outlines" 2>/dev/null || echo "")
    PREV_OUTLINE=$(ls -dt "$BASE_PATH"/outlines_v* 2>/dev/null | grep -v "${CURRENT_OUTLINE:-^$}" | head -1 || true)

    if [ -z "$PREV_TUNNEL" ] || [ -z "$PREV_BRIDGE" ]; then
        print_error "No previous version found"
        exit 1
    fi

    echo "Current Tunnels: $CURRENT_TUNNEL"
    echo "Current Bridges: $CURRENT_BRIDGE"
    echo "Current Outlines: ${CURRENT_OUTLINE:-plain folder}"
    echo ""
    echo "Rollback to:"
    echo "  Tunnels: $(basename $PREV_TUNNEL)"
    echo "  Bridges: $(basename $PREV_BRIDGE)"
    if [ -n "$PREV_OUTLINE" ]; then
        echo "  Outlines: $(basename $PREV_OUTLINE)"
    else
        echo "  Outlines: kept (no previous version)"
    fi
    echo ""
    
    read -p "Are you sure you want to rollback? (yes/no): " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo "Rollback cancelled"
        exit 0
    fi
    
    swap_link "$(basename $PREV_TUNNEL)" "$COUNTRIES_PATH/tunnels"
    swap_link "$(basename $PREV_BRIDGE)" "$COUNTRIES_PATH/bridges"
    if [ -n "$PREV_OUTLINE" ] && [ -L "$BASE_PATH/outlines" ]; then
        swap_link "$(basename $PREV_OUTLINE)" "$BASE_PATH/outlines"
    fi

    print_success "Rollback completed"
    print_success "Tunnels: $(readlink $COUNTRIES_PATH/tunnels)"
    print_success "Bridges: $(readlink $COUNTRIES_PATH/bridges)"
    print_success "Outlines: $(readlink $BASE_PATH/outlines 2>/dev/null || echo 'plain folder')"
}

# Command: clean-stale-lock
cmd_clean_stale_lock() {
    if [ ! -f "$LOCK_FILE" ]; then
        print_success "No lock file present"
        exit 0
    fi
    
    print_header "Clean Stale Lock"
    
    LOCK_TIME=$(tail -1 $LOCK_FILE 2>/dev/null || echo "0")
    CURRENT_TIME=$(date +%s)
    AGE=$((CURRENT_TIME - ${LOCK_TIME%.*}))
    
    echo "Lock age: $((AGE / 3600)) hours $((AGE % 3600 / 60)) minutes"
    
    if [ $AGE -lt 86400 ]; then
        print_warning "Lock is less than 24 hours old"
        read -p "Remove anyway? (yes/no): " CONFIRM
        if [ "$CONFIRM" != "yes" ]; then
            echo "Cancelled"
            exit 0
        fi
    fi
    
    rm -f "$LOCK_FILE"
    print_success "Lock file removed"
}

# Command: cleanup
cmd_cleanup() {
    if [ -f "$LOCK_FILE" ]; then
        print_error "Cannot cleanup: Update in progress"
        exit 1
    fi
    
    print_header "Cleanup Old Versions"
    
    KEEP_VERSIONS=2
    echo "Keeping latest $KEEP_VERSIONS versions"
    echo ""
    
    # Tunnels
    echo "Tunnel versions:"
    TUNNEL_VERSIONS=($(ls -dt "$COUNTRIES_PATH"/tunnels_v* 2>/dev/null))
    TUNNEL_COUNT=${#TUNNEL_VERSIONS[@]}
    
    if [ $TUNNEL_COUNT -gt $KEEP_VERSIONS ]; then
        echo "  Found: $TUNNEL_COUNT versions"
        for ((i=KEEP_VERSIONS; i<TUNNEL_COUNT; i++)); do
            VERSION=$(basename "${TUNNEL_VERSIONS[$i]}")
            SIZE=$(du -sh "${TUNNEL_VERSIONS[$i]}" | cut -f1)
            echo "  Will remove: $VERSION ($SIZE)"
        done
    else
        echo "  Found: $TUNNEL_COUNT versions (nothing to clean)"
    fi
    
    # Bridges
    echo ""
    echo "Bridge versions:"
    BRIDGE_VERSIONS=($(ls -dt "$COUNTRIES_PATH"/bridges_v* 2>/dev/null))
    BRIDGE_COUNT=${#BRIDGE_VERSIONS[@]}
    
    if [ $BRIDGE_COUNT -gt $KEEP_VERSIONS ]; then
        echo "  Found: $BRIDGE_COUNT versions"
        for ((i=KEEP_VERSIONS; i<BRIDGE_COUNT; i++)); do
            VERSION=$(basename "${BRIDGE_VERSIONS[$i]}")
            SIZE=$(du -sh "${BRIDGE_VERSIONS[$i]}" | cut -f1)
            echo "  Will remove: $VERSION ($SIZE)"
        done
    else
        echo "  Found: $BRIDGE_COUNT versions (nothing to clean)"
    fi

    # Outlines
    echo ""
    echo "Outline versions:"
    OUTLINE_VERSIONS=($(ls -dt "$BASE_PATH"/outlines_v* 2>/dev/null))
    OUTLINE_COUNT=${#OUTLINE_VERSIONS[@]}

    if [ $OUTLINE_COUNT -gt $KEEP_VERSIONS ]; then
        echo "  Found: $OUTLINE_COUNT versions"
        for ((i=KEEP_VERSIONS; i<OUTLINE_COUNT; i++)); do
            VERSION=$(basename "${OUTLINE_VERSIONS[$i]}")
            SIZE=$(du -sh "${OUTLINE_VERSIONS[$i]}" | cut -f1)
            echo "  Will remove: $VERSION ($SIZE)"
        done
    else
        echo "  Found: $OUTLINE_COUNT versions (nothing to clean)"
    fi

    if [ $TUNNEL_COUNT -le $KEEP_VERSIONS ] && [ $BRIDGE_COUNT -le $KEEP_VERSIONS ] && [ $OUTLINE_COUNT -le $KEEP_VERSIONS ]; then
        print_success "Nothing to clean"
        exit 0
    fi
    
    echo ""
    read -p "Proceed with cleanup? (yes/no): " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo "Cleanup cancelled"
        exit 0
    fi
    
    # Remove old tunnels
    for ((i=KEEP_VERSIONS; i<TUNNEL_COUNT; i++)); do
        rm -rf "${TUNNEL_VERSIONS[$i]}"
        print_success "Removed: $(basename ${TUNNEL_VERSIONS[$i]})"
    done
    
    # Remove old bridges
    for ((i=KEEP_VERSIONS; i<BRIDGE_COUNT; i++)); do
        rm -rf "${BRIDGE_VERSIONS[$i]}"
        print_success "Removed: $(basename ${BRIDGE_VERSIONS[$i]})"
    done

    # Remove old outlines
    for ((i=KEEP_VERSIONS; i<OUTLINE_COUNT; i++)); do
        rm -rf "${OUTLINE_VERSIONS[$i]}"
        print_success "Removed: $(basename ${OUTLINE_VERSIONS[$i]})"
    done

    print_success "Cleanup completed"
}

# Command: restore
cmd_restore() {
    if [ -f "$LOCK_FILE" ]; then
        print_error "Cannot restore: Update in progress"
        exit 1
    fi
    
    print_header "Restore from Archive"
    
    echo "Available archives:"
    echo ""
    echo "Tunnels:"
    ls -lht "$ARCHIVE_PATH/tunnels"/*.zip 2>/dev/null | head -5 | nl -w2 -s'. '
    
    echo ""
    echo "Bridges:"
    ls -lht "$ARCHIVE_PATH/bridges"/*.zip 2>/dev/null | head -5 | nl -w2 -s'. '

    echo ""
    echo "Outlines:"
    ls -lht "$ARCHIVE_PATH/outlines"/*.zip 2>/dev/null | head -5 | nl -w2 -s'. '

    echo ""
    read -p "Restore latest archive? (yes/no): " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo "Restore cancelled"
        exit 0
    fi
    
    # Restored data become regular versions (<name>_v<timestamp>): seen by rollback, list-versions and cleanup
    TIMESTAMP=$(date +%Y%m%d-%H%M%S)
    RESTORE_TUNNEL="$COUNTRIES_PATH/tunnels_v$TIMESTAMP"
    RESTORE_BRIDGE="$COUNTRIES_PATH/bridges_v$TIMESTAMP"
    
    mkdir -p "$RESTORE_TUNNEL"
    mkdir -p "$RESTORE_BRIDGE"
    
    LATEST_TUNNEL=$(ls -t "$ARCHIVE_PATH/tunnels"/*.zip 2>/dev/null | head -1)
    LATEST_BRIDGE=$(ls -t "$ARCHIVE_PATH/bridges"/*.zip 2>/dev/null | head -1)
    
    echo "Extracting tunnels..."
    extract_flat "$LATEST_TUNNEL" "$RESTORE_TUNNEL"

    echo "Extracting bridges..."
    extract_flat "$LATEST_BRIDGE" "$RESTORE_BRIDGE"

    RESTORE_OUTLINE="$BASE_PATH/outlines_v$TIMESTAMP"
    LATEST_OUTLINE=$(ls -t "$ARCHIVE_PATH/outlines"/*.zip 2>/dev/null | head -1 || true)
    if [ -n "$LATEST_OUTLINE" ]; then
        echo "Extracting outlines..."
        mkdir -p "$RESTORE_OUTLINE"
        extract_flat "$LATEST_OUTLINE" "$RESTORE_OUTLINE"
    fi

    echo ""
    echo "Restored to:"
    echo "  $RESTORE_TUNNEL"
    echo "  $RESTORE_BRIDGE"
    if [ -n "$LATEST_OUTLINE" ]; then echo "  $RESTORE_OUTLINE"; fi
    echo ""

    read -p "Make these the current versions? (yes/no): " CONFIRM
    if [ "$CONFIRM" = "yes" ]; then
        swap_link "$(basename $RESTORE_TUNNEL)" "$COUNTRIES_PATH/tunnels"
        swap_link "$(basename $RESTORE_BRIDGE)" "$COUNTRIES_PATH/bridges"
        if [ -n "$LATEST_OUTLINE" ] && [ -L "$BASE_PATH/outlines" ]; then
            swap_link "$(basename $RESTORE_OUTLINE)" "$BASE_PATH/outlines"
        elif [ -n "$LATEST_OUTLINE" ]; then
            print_warning "Outlines are a plain folder: restored to $(basename $RESTORE_OUTLINE), not activated"
        fi
        print_success "Restored and activated"
    else
        echo "Restored but not activated. You can manually activate with:"
        echo "  ln -sfn $(basename $RESTORE_TUNNEL) $COUNTRIES_PATH/tunnels"
        echo "  ln -sfn $(basename $RESTORE_BRIDGE) $COUNTRIES_PATH/bridges"
        if [ -n "$LATEST_OUTLINE" ]; then echo "  ln -sfn $(basename $RESTORE_OUTLINE) $BASE_PATH/outlines"; fi
    fi
}

# Command: verify
cmd_verify() {
    print_header "System Verification"
    
    ERRORS=0
    
    # Check symlinks
    echo "Checking symlinks..."
    if [ -L "$COUNTRIES_PATH/tunnels" ]; then
        TARGET=$(readlink "$COUNTRIES_PATH/tunnels")
        if [ -d "$COUNTRIES_PATH/$TARGET" ]; then
            print_success "Tunnels symlink OK -> $TARGET"
        else
            print_error "Tunnels symlink broken -> $TARGET (does not exist)"
            ERRORS=$((ERRORS + 1))
        fi
    else
        print_error "Tunnels symlink missing"
        ERRORS=$((ERRORS + 1))
    fi
    
    if [ -L "$COUNTRIES_PATH/bridges" ]; then
        TARGET=$(readlink "$COUNTRIES_PATH/bridges")
        if [ -d "$COUNTRIES_PATH/$TARGET" ]; then
            print_success "Bridges symlink OK -> $TARGET"
        else
            print_error "Bridges symlink broken -> $TARGET (does not exist)"
            ERRORS=$((ERRORS + 1))
        fi
    else
        print_error "Bridges symlink missing"
        ERRORS=$((ERRORS + 1))
    fi

    if [ -L "$BASE_PATH/outlines" ]; then
        TARGET=$(readlink "$BASE_PATH/outlines")
        if [ -d "$BASE_PATH/$TARGET" ]; then
            print_success "Outlines symlink OK -> $TARGET"
        else
            print_error "Outlines symlink broken -> $TARGET (does not exist)"
            ERRORS=$((ERRORS + 1))
        fi
    elif [ -d "$BASE_PATH/outlines" ]; then
        print_warning "Outlines is a plain folder (versioned at the next update)"
    else
        print_error "Outlines missing"
        ERRORS=$((ERRORS + 1))
    fi

    # Check for required files
    echo ""
    echo "Checking data files..."
    TUNNEL_FILES=$(find "$(readlink -f $COUNTRIES_PATH/tunnels 2>/dev/null || echo /nonexistent)" -name "*_MultiPoly.shp" 2>/dev/null | wc -l)
    BRIDGE_FILES=$(find "$(readlink -f $COUNTRIES_PATH/bridges 2>/dev/null || echo /nonexistent)" -name "*_MultiPoly.shp" 2>/dev/null | wc -l)
    OUTLINE_FILES=$(find "$(readlink -f $BASE_PATH/outlines 2>/dev/null || echo /nonexistent)" -maxdepth 1 -name "*_MultiPoly.shp" 2>/dev/null | wc -l)

    if [ $TUNNEL_FILES -gt 0 ]; then
        print_success "Tunnel files OK ($TUNNEL_FILES shapefiles)"
    else
        print_error "No tunnel shapefiles found"
        ERRORS=$((ERRORS + 1))
    fi
    
    if [ $BRIDGE_FILES -gt 0 ]; then
        print_success "Bridge files OK ($BRIDGE_FILES shapefiles)"
    else
        print_error "No bridge shapefiles found"
        ERRORS=$((ERRORS + 1))
    fi

    if [ $OUTLINE_FILES -gt 0 ]; then
        print_success "Outline files OK ($OUTLINE_FILES shapefiles)"
    else
        print_error "No outline shapefiles found"
        ERRORS=$((ERRORS + 1))
    fi

    # Check archives
    echo ""
    echo "Checking archives..."
    TUNNEL_ARCHIVES=$(ls "$ARCHIVE_PATH/tunnels"/*.zip 2>/dev/null | wc -l)
    BRIDGE_ARCHIVES=$(ls "$ARCHIVE_PATH/bridges"/*.zip 2>/dev/null | wc -l)
    
    if [ $TUNNEL_ARCHIVES -gt 0 ]; then
        print_success "Tunnel archives OK ($TUNNEL_ARCHIVES archives)"
    else
        print_warning "No tunnel archives found"
    fi
    
    if [ $BRIDGE_ARCHIVES -gt 0 ]; then
        print_success "Bridge archives OK ($BRIDGE_ARCHIVES archives)"
    else
        print_warning "No bridge archives found"
    fi

    OUTLINE_ARCHIVES=$(ls "$ARCHIVE_PATH/outlines"/*.zip 2>/dev/null | wc -l)
    if [ $OUTLINE_ARCHIVES -gt 0 ]; then
        print_success "Outline archives OK ($OUTLINE_ARCHIVES archives)"
    else
        print_warning "No outline archives found"
    fi

    echo ""
    if [ $ERRORS -eq 0 ]; then
        print_success "System verification passed"
    else
        print_error "System verification failed with $ERRORS errors"
        exit 1
    fi
}

# Main
case "${1:-}" in
    status)
        cmd_status
        ;;
    list-versions)
        cmd_list_versions
        ;;
    rollback)
        cmd_rollback
        ;;
    clean-stale-lock)
        cmd_clean_stale_lock
        ;;
    cleanup)
        cmd_cleanup
        ;;
    restore)
        cmd_restore
        ;;
    verify)
        cmd_verify
        ;;
    *)
        echo "OSM Data Management Helper"
        echo ""
        echo "Usage: $0 [command]"
        echo ""
        echo "Commands:"
        echo "  status            - Show current system status"
        echo "  list-versions     - List all available versions"
        echo "  rollback          - Rollback to previous version"
        echo "  clean-stale-lock  - Remove stale update lock"
        echo "  cleanup           - Remove old versions (keep latest 2)"
        echo "  restore           - Restore from archive"
        echo "  verify            - Verify system integrity"
        echo ""
        exit 1
        ;;
esac
