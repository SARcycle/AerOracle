"""CPU and RAM use of operations (resource_monitor.py) in the usage statistics database of tracker.py."""

import sqlite3
from pathlib import Path
from contextlib import contextmanager
import datetime
from resource_monitor import track_resources, ResourceMetrics

# Use same database as main tracker
DB_PATH = Path(__file__).parent / "usage.db"


@contextmanager
def get_db():
    """Context manager for database connections"""
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def init_resource_tables():
    """Add resource tracking tables to existing database"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Resource metrics table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS resource_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                operation_type TEXT,
                duration_seconds REAL,
                
                -- CPU metrics
                cpu_min REAL,
                cpu_max REAL,
                cpu_mean REAL,
                cpu_p95 REAL,
                
                -- Memory metrics (MB)
                memory_min_mb REAL,
                memory_max_mb REAL,
                memory_mean_mb REAL,
                memory_p95_mb REAL,
                
                -- Memory percent
                memory_percent_max REAL,
                
                -- Associated with API call?
                api_call_id INTEGER,
                FOREIGN KEY (api_call_id) REFERENCES api_calls(id)
            )
        """)
        
        # Index for faster queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_resource_timestamp 
            ON resource_metrics(timestamp)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_resource_operation 
            ON resource_metrics(operation_type)
        """)
        
        conn.commit()


def log_resource_usage(metrics: ResourceMetrics, operation_type: str = "api_call", api_call_id: int = None):
    """
    Store resource metrics in database
    
    Parameters:
    -----------
    metrics : ResourceMetrics
        Measured resource usage
    operation_type : str
        Type of operation (e.g., "api_call", "route_analysis", "optimization")
    api_call_id : int, optional
        Associated API call ID from api_calls table
    """
    with get_db() as conn:
        cursor = conn.cursor()
        # Local time (the column DEFAULT would be UTC)
        timestamp = datetime.datetime.now()
        cursor.execute("""
            INSERT INTO resource_metrics (
                timestamp, operation_type, duration_seconds,
                cpu_min, cpu_max, cpu_mean, cpu_p95,
                memory_min_mb, memory_max_mb, memory_mean_mb, memory_p95_mb,
                memory_percent_max,
                api_call_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp, operation_type, metrics.duration_seconds,
            metrics.cpu_min, metrics.cpu_max, metrics.cpu_mean, metrics.cpu_p95,
            metrics.memory_min_mb, metrics.memory_max_mb, metrics.memory_mean_mb, metrics.memory_p95_mb,
            metrics.memory_percent_max,
            api_call_id
        ))
        conn.commit()
        return cursor.lastrowid


def get_resource_stats(days: int = 7, operation_type: str = None):
    """
    Get resource usage statistics
    
    Parameters:
    -----------
    days : int
        Look back period
    operation_type : str, optional
        Filter by operation type
    
    Returns:
    --------
    dict with meaningful metrics (peaks, p95, trends)
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
        
        # Build query
        where_clause = "WHERE timestamp > ?"
        params = [cutoff]
        
        if operation_type:
            where_clause += " AND operation_type = ?"
            params.append(operation_type)
        
        # Get aggregated stats
        cursor.execute(f"""
            SELECT 
                COUNT(*) as operations,
                AVG(duration_seconds) as avg_duration,
                MAX(duration_seconds) as max_duration,
                
                -- CPU stats (what matters: peaks!)
                MAX(cpu_max) as peak_cpu,
                AVG(cpu_max) as avg_peak_cpu,
                AVG(cpu_p95) as avg_p95_cpu,
                
                -- Memory stats (what matters: peaks!)
                MAX(memory_max_mb) as peak_memory_mb,
                AVG(memory_max_mb) as avg_peak_memory_mb,
                AVG(memory_p95_mb) as avg_p95_memory_mb,
                MAX(memory_percent_max) as peak_memory_percent
                
            FROM resource_metrics
            {where_clause}
        """, params)
        
        row = cursor.fetchone()
        
        if not row or row[0] == 0:
            return None
        
        # Get bottleneck count (operations that hit high resource usage)
        cursor.execute(f"""
            SELECT 
                SUM(CASE WHEN cpu_max > 80 THEN 1 ELSE 0 END) as cpu_bottlenecks,
                SUM(CASE WHEN memory_percent_max > 80 THEN 1 ELSE 0 END) as memory_bottlenecks
            FROM resource_metrics
            {where_clause}
        """, params)
        
        bottleneck_row = cursor.fetchone()
        
        # Get hourly trends (peak usage by hour)
        cursor.execute(f"""
            SELECT 
                strftime('%H', timestamp) as hour,
                MAX(cpu_max) as peak_cpu,
                MAX(memory_max_mb) as peak_memory
            FROM resource_metrics
            {where_clause}
            GROUP BY hour
            ORDER BY hour
        """, params)
        
        hourly_peaks = cursor.fetchall()
        
        return {
            'operations': row[0],
            'avg_duration': row[1],
            'max_duration': row[2],
            
            # CPU metrics
            'peak_cpu': row[3],              # Highest CPU seen
            'avg_peak_cpu': row[4],          # Average of peak CPUs
            'avg_p95_cpu': row[5],           # Average of 95th percentiles
            
            # Memory metrics
            'peak_memory_mb': row[6],        # Highest RAM seen
            'avg_peak_memory_mb': row[7],    # Average of peak RAMs
            'avg_p95_memory_mb': row[8],     # Average of 95th percentiles
            'peak_memory_percent': row[9],   # Highest % of total RAM
            
            # Bottlenecks
            'cpu_bottlenecks': bottleneck_row[0],
            'memory_bottlenecks': bottleneck_row[1],
            'bottleneck_percent': (bottleneck_row[0] + bottleneck_row[1]) / (2 * row[0]) * 100 if row[0] > 0 else 0,
            
            # Trends
            'hourly_peaks': hourly_peaks
        }


def get_recent_operations(limit: int = 20):
    """Get recent operations with resource usage"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                timestamp,
                operation_type,
                duration_seconds,
                cpu_max,
                cpu_mean,
                memory_max_mb,
                memory_mean_mb,
                memory_percent_max
            FROM resource_metrics
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        
        return cursor.fetchall()


# --- Integrated Usage - Track API Calls WITH Resources ---

def track_api_call_with_resources(tracker_module, params, success=True, call_type="weather"):
    """
    Log an API call like tracker.log_api_call(), after the call has completed, and return its id in the api_calls
    table, so that log_resource_usage() can refer to it. tracker_module is not used (kept for the callers).
    """
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Same counting as tracker.log_api_call()
        locations = len(params.get("latitude", []))
        hourly_vars = len(params.get("hourly", []))
        daily_vars = len(params.get("daily", []))
        minutely_15_vars = len(params.get("minutely_15", []))
        
        # Calculate time range
        if params.get("forecast_days") is not None:
            time_range_days = params["forecast_days"]
        elif "start_date" in params and "end_date" in params:
            from datetime import datetime as dt
            try:
                start = dt.strptime(params["start_date"], "%Y-%m-%d")
                end = dt.strptime(params["end_date"], "%Y-%m-%d")
                time_range_days = (end - start).days + 1
            except:
                time_range_days = 1
        else:
            time_range_days = 1
        
        # Calculate cost
        total_vars = hourly_vars + daily_vars + minutely_15_vars
        base_cost = locations * 1.0
        var_multiplier = max(1.0, total_vars / 10.0)
        weeks = time_range_days / 7.0
        time_multiplier = max(1.0, weeks / 2.0)
        estimated_cost = base_cost * var_multiplier * time_multiplier
        
        # Local time (the column DEFAULT would be UTC)
        timestamp = datetime.datetime.now()
        
        # Insert API call
        cursor.execute("""
            INSERT INTO api_calls 
            (timestamp, call_type, success, locations, hourly_vars, daily_vars, 
             minutely_15_vars, time_range_days, estimated_cost)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (timestamp, call_type, success, locations, hourly_vars, daily_vars,
              minutely_15_vars, time_range_days, estimated_cost))
        
        api_call_id = cursor.lastrowid
        conn.commit()
    
    return api_call_id


# Initialize tables on import
init_resource_tables()
