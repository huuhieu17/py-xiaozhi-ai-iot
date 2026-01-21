#!/bin/bash
# =============================================================================
#            SMART C AI - COMMON SHELL FUNCTIONS
# =============================================================================
# Source this file in other scripts: source scripts/common.sh
# =============================================================================

# Colors
export RED='\033[0;31m'
export GREEN='\033[0;32m'
export YELLOW='\033[1;33m'
export BLUE='\033[0;34m'
export CYAN='\033[0;36m'
export NC='\033[0m'

# Application directory detection
detect_app_home() {
    if [ -d "$HOME/.digits" ]; then
        echo "$HOME/.digits"
    elif [ -d "$HOME/.xiaozhi" ]; then
        echo "$HOME/.xiaozhi"
    else
        echo ""
    fi
}

# Set APP_HOME variable
APP_HOME=$(detect_app_home)
export APP_HOME

# Logging functions
log() {
    echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[$(date '+%H:%M:%S')] ⚠️  $1${NC}"
}

log_error() {
    echo -e "${RED}[$(date '+%H:%M:%S')] ❌ $1${NC}"
}

log_info() {
    echo -e "${CYAN}[$(date '+%H:%M:%S')] ℹ️  $1${NC}"
}

# Check if running as root
check_not_root() {
    if [[ $EUID -eq 0 ]]; then
        log_error "Không chạy script này với sudo/root!"
        log "Chạy lại: bash $0"
        exit 1
    fi
}

# Check if running as root (required)
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "Script này cần chạy với sudo!"
        log "Chạy: sudo bash $0"
        exit 1
    fi
}

# Check Raspberry Pi
check_raspberry_pi() {
    if [ -f /proc/device-tree/model ]; then
        MODEL=$(cat /proc/device-tree/model)
        log "Phát hiện: $MODEL"
        return 0
    else
        log_warn "Không phát hiện Raspberry Pi, tiếp tục..."
        return 1
    fi
}

# Check network connectivity
check_network() {
    if ping -c 1 -W 3 github.com &>/dev/null; then
        return 0
    elif curl -s --connect-timeout 5 https://github.com &>/dev/null; then
        return 0
    else
        return 1
    fi
}

# Backup config files
backup_config() {
    local backup_dir="${1:-/tmp/smartc_backup_$(date +%Y%m%d_%H%M%S)}"
    mkdir -p "$backup_dir"
    
    if [ -f "$APP_HOME/config/config.json" ]; then
        cp "$APP_HOME/config/config.json" "$backup_dir/"
        log "Backup: config.json"
    fi
    
    if [ -f "$APP_HOME/config/efuse.json" ]; then
        cp "$APP_HOME/config/efuse.json" "$backup_dir/"
        log "Backup: efuse.json"
    fi
    
    if [ -f "$APP_HOME/config/.first_run_done" ]; then
        cp "$APP_HOME/config/.first_run_done" "$backup_dir/"
        log "Backup: .first_run_done"
    fi
    
    echo "$backup_dir"
}

# Restore config files
restore_config() {
    local backup_dir="$1"
    
    if [ -z "$backup_dir" ] || [ ! -d "$backup_dir" ]; then
        log_error "Backup directory not found: $backup_dir"
        return 1
    fi
    
    if [ -f "$backup_dir/config.json" ]; then
        cp "$backup_dir/config.json" "$APP_HOME/config/"
        log "Restored: config.json"
    fi
    
    if [ -f "$backup_dir/efuse.json" ]; then
        cp "$backup_dir/efuse.json" "$APP_HOME/config/"
        log "Restored: efuse.json"
    fi
    
    if [ -f "$backup_dir/.first_run_done" ]; then
        cp "$backup_dir/.first_run_done" "$APP_HOME/config/"
        log "Restored: .first_run_done"
    fi
}

# Set executable permissions
set_permissions() {
    chmod +x "$APP_HOME/run.sh" 2>/dev/null || true
    chmod +x "$APP_HOME/run_cli.sh" 2>/dev/null || true
    chmod +x "$APP_HOME/update.sh" 2>/dev/null || true
    chmod +x "$APP_HOME/auto_update.sh" 2>/dev/null || true
    chmod +x "$APP_HOME/scripts/"*.sh 2>/dev/null || true
}

# Stop running app
stop_app() {
    pkill -f "python3 main.py" 2>/dev/null || true
    pkill -f "python3 $APP_HOME/main.py" 2>/dev/null || true
    sudo systemctl stop smartc 2>/dev/null || true
    sleep 1
}

# Start app
start_app() {
    if systemctl is-enabled smartc 2>/dev/null | grep -q "enabled"; then
        sudo systemctl start smartc
        log "Started smartc service"
    elif [ -f "$APP_HOME/run.sh" ]; then
        nohup "$APP_HOME/run.sh" > /dev/null 2>&1 &
        log "Started app via run.sh"
    fi
}

# Print banner
print_banner() {
    local title="${1:-SMART C AI}"
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║                                                                  ║"
    printf "║     🤖  %-55s ║\n" "$title"
    echo "║                                                                  ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}
