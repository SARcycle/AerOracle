"""
OPTIMAL tracking for AerOracle

Best of both worlds:
1. Automatic parameter extraction from params dict (good UX)
2. No session_id requirement (works from CLI and Streamlit)
3. Just uses timestamp for tracking
"""

import sqlite3
import datetime
from pathlib import Path
from contextlib import contextmanager

# Database location
DB_PATH = Path(__file__).parent / "usage.db"


@contextmanager
def get_db():
    """Context manager for database connections"""
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize the database with required tables"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # API calls table - no session_id needed
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                call_type TEXT,
                success BOOLEAN,
                locations INTEGER DEFAULT 1,
                hourly_vars INTEGER DEFAULT 0,
                daily_vars INTEGER DEFAULT 0,
                minutely_15_vars INTEGER DEFAULT 0,
                time_range_days REAL DEFAULT 1,
                estimated_cost REAL DEFAULT 1.0
            )
        """)
        
        # Route analysis table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                route_distance_km REAL,
                route_elevation_gain_m REAL,
                analysis_success BOOLEAN
            )
        """)
        
        conn.commit()


def _calculate_api_cost(total_vars, time_range_days, locations=1):
    """
    Calculate API cost using Open-Meteo's formula
    
    From Open-Meteo docs:
    - Base: 1.0 per location
    - If vars > 10: multiply by (vars / 10)
    - If time > 2 weeks: multiply by (weeks / 2)
    """
    base_cost = locations * 1.0
    var_multiplier = max(1.0, total_vars / 10.0)
    weeks = time_range_days / 7.0
    time_multiplier = max(1.0, weeks / 2.0)
    
    return base_cost * var_multiplier * time_multiplier


def _extract_params_from_request(params):
    """
    Automatically extract relevant parameters from API request
    
    Parameters:
    -----------
    params : dict
        The params dict you pass to openmeteo.weather_api()
    
    Returns:
    --------
    dict with: locations, hourly_vars, daily_vars, minutely_15_vars, time_range_days
    """
    # Count locations
    lat = params.get("latitude", [])
    locations = len(lat) if isinstance(lat, list) else 1
    
    # Count variables (including minutely_15!)
    hourly_vars = len(params.get("hourly", []))
    daily_vars = len(params.get("daily", []))
    minutely_15_vars = len(params.get("minutely_15", []))
    
    # Calculate time range
    if "forecast_days" in params:
        time_range_days = params["forecast_days"]
    elif "past_days" in params:
        time_range_days = params["past_days"]
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
    
    return {
        "locations": locations,
        "hourly_vars": hourly_vars,
        "daily_vars": daily_vars,
        "minutely_15_vars": minutely_15_vars,
        "time_range_days": time_range_days
    }


def log_api_call(params=None, call_type="weather", success=True, 
                 locations=None, hourly_vars=None, daily_vars=None, 
                 minutely_15_vars=None, time_range_days=None):
    """
    Log an API call with AUTOMATIC parameter extraction
    
    NO session_id needed - works from anywhere!
    
    EASY MODE (recommended):
    ------------------------
    Just pass your request params dict:
    
        params = {
            "latitude": [47.5, 47.51, ...],
            "longitude": [8.5, 8.51, ...],
            "hourly": ["wind_speed_10m", "wind_direction_10m"],
            "minutely_15": ["temperature_2m"],
            "start_date": "2026-01-10",
            "end_date": "2026-01-10"
        }
        
        tracker.log_api_call(params=params, success=True)
        
    That's it! Works from Streamlit, CLI, anywhere.
    
    
    MANUAL MODE (if needed):
    ------------------------
    You can still pass individual parameters:
    
        tracker.log_api_call(
            call_type="forecast",
            success=True,
            locations=100,
            hourly_vars=2,
            daily_vars=0,
            minutely_15_vars=1,
            time_range_days=7
        )
    
    
    Parameters:
    -----------
    params : dict, optional
        The params dict you pass to openmeteo.weather_api()
        If provided, we extract everything automatically
    call_type : str
        Type of API call ("weather", "forecast", "historical")
    success : bool
        Whether the call succeeded
    locations : int, optional
        Manual override for number of locations
    hourly_vars : int, optional
        Manual override for number of hourly variables
    daily_vars : int, optional
        Manual override for number of daily variables
    minutely_15_vars : int, optional
        Manual override for number of minutely_15 variables
    time_range_days : float, optional
        Manual override for time range in days
    """
    
    # Auto-extract from params if provided
    if params is not None:
        extracted = _extract_params_from_request(params)
        # Use manual overrides if provided, otherwise use extracted values
        locations = locations if locations is not None else extracted["locations"]
        hourly_vars = hourly_vars if hourly_vars is not None else extracted["hourly_vars"]
        daily_vars = daily_vars if daily_vars is not None else extracted["daily_vars"]
        minutely_15_vars = minutely_15_vars if minutely_15_vars is not None else extracted["minutely_15_vars"]
        time_range_days = time_range_days if time_range_days is not None else extracted["time_range_days"]
    else:
        # Use defaults if nothing provided
        locations = locations or 1
        hourly_vars = hourly_vars or 0
        daily_vars = daily_vars or 0
        minutely_15_vars = minutely_15_vars or 0
        time_range_days = time_range_days or 1
    
    # Calculate API cost using Open-Meteo's formula
    total_vars = hourly_vars + daily_vars + minutely_15_vars
    estimated_cost = _calculate_api_cost(total_vars, time_range_days, locations)
    
    # Local time (the column DEFAULT would be UTC)
    timestamp = datetime.datetime.now()
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO api_calls 
               (timestamp, call_type, success, locations, hourly_vars, 
                daily_vars, minutely_15_vars, time_range_days, estimated_cost) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, call_type, success, locations, hourly_vars, 
             daily_vars, minutely_15_vars, time_range_days, estimated_cost)
        )
        conn.commit()


def log_analysis(distance_km=None, elevation_gain_m=None, success=True):
    """Log a route analysis"""
    # Local time (the column DEFAULT would be UTC)
    timestamp = datetime.datetime.now()
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO analyses (timestamp, route_distance_km, 
               route_elevation_gain_m, analysis_success) VALUES (?, ?, ?, ?)""",
            (timestamp, distance_km, elevation_gain_m, success)
        )
        conn.commit()


def get_stats(days=7):
    """Get usage statistics for the last N days"""
    with get_db() as conn:
        cursor = conn.cursor()
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
        
        # Total API requests
        cursor.execute(
            "SELECT COUNT(*) FROM api_calls WHERE timestamp > ?",
            (cutoff,)
        )
        total_requests = cursor.fetchone()[0]
        
        # API calls by day with estimated costs
        cursor.execute("""
            SELECT DATE(timestamp) as date, 
                   COUNT(*) as http_requests,
                   SUM(estimated_cost) as estimated_api_cost,
                   SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful
            FROM api_calls 
            WHERE timestamp > ?
            GROUP BY DATE(timestamp)
            ORDER BY date DESC
        """, (cutoff,))
        api_calls_by_day = cursor.fetchall()
        
        # Today's API usage
        cursor.execute("""
            SELECT COUNT(*), SUM(estimated_cost) 
            FROM api_calls 
            WHERE DATE(timestamp) = DATE('now')
        """)
        today_http_requests, today_estimated_cost = cursor.fetchone()
        today_estimated_cost = today_estimated_cost or 0
        
        # Average daily cost (for the period)
        cursor.execute("""
            SELECT AVG(daily_cost) FROM (
                SELECT DATE(timestamp) as date, SUM(estimated_cost) as daily_cost
                FROM api_calls
                WHERE timestamp > ?
                GROUP BY DATE(timestamp)
            )
        """, (cutoff,))
        avg_daily_cost = cursor.fetchone()[0] or 0
        
        # Maximum daily cost (all time)
        cursor.execute("""
            SELECT MAX(daily_cost), date FROM (
                SELECT DATE(timestamp) as date, SUM(estimated_cost) as daily_cost
                FROM api_calls
                GROUP BY DATE(timestamp)
            )
        """)
        max_result = cursor.fetchone()
        max_daily_cost = max_result[0] or 0
        max_daily_cost_date = max_result[1] if max_result[0] else None
        
        # Days above 90% threshold (9000 API calls)
        cursor.execute("""
            SELECT COUNT(*) FROM (
                SELECT DATE(timestamp) as date, SUM(estimated_cost) as daily_cost
                FROM api_calls
                GROUP BY DATE(timestamp)
                HAVING daily_cost > 9000
            )
        """)
        days_above_90_percent = cursor.fetchone()[0] or 0
        
        # Days above 75% threshold (7500 API calls)
        cursor.execute("""
            SELECT COUNT(*) FROM (
                SELECT DATE(timestamp) as date, SUM(estimated_cost) as daily_cost
                FROM api_calls
                GROUP BY DATE(timestamp)
                HAVING daily_cost > 7500
            )
        """)
        days_above_75_percent = cursor.fetchone()[0] or 0
        
        # Successful analyses
        cursor.execute(
            "SELECT COUNT(*) FROM analyses WHERE timestamp > ? AND analysis_success = 1",
            (cutoff,)
        )
        successful_analyses = cursor.fetchone()[0]
        
        # Today's analyses
        cursor.execute("""
            SELECT COUNT(*) 
            FROM analyses 
            WHERE DATE(timestamp) = DATE('now') AND analysis_success = 1
        """)
        today_analyses = cursor.fetchone()[0] or 0
        
        # Average daily analyses (for the period)
        cursor.execute("""
            SELECT AVG(daily_analyses) FROM (
                SELECT DATE(timestamp) as date, COUNT(*) as daily_analyses
                FROM analyses
                WHERE timestamp > ? AND analysis_success = 1
                GROUP BY DATE(timestamp)
            )
        """, (cutoff,))
        avg_daily_analyses = cursor.fetchone()[0] or 0
        
        # Average daily HTTP requests (for the period)
        cursor.execute("""
            SELECT AVG(daily_requests) FROM (
                SELECT DATE(timestamp) as date, COUNT(*) as daily_requests
                FROM api_calls
                WHERE timestamp > ?
                GROUP BY DATE(timestamp)
            )
        """, (cutoff,))
        avg_daily_requests = cursor.fetchone()[0] or 0
        
        # All analyses, for the success rate
        cursor.execute(
            "SELECT COUNT(*) FROM analyses WHERE timestamp > ?",
            (cutoff,)
        )
        total_analyses = cursor.fetchone()[0]
        
        # Average route metrics
        cursor.execute("""
            SELECT AVG(route_distance_km), AVG(route_elevation_gain_m)
            FROM analyses 
            WHERE timestamp > ? AND analysis_success = 1
        """, (cutoff,))
        avg_metrics = cursor.fetchone()
        
        # Total distance and elevation
        cursor.execute("""
            SELECT SUM(route_distance_km), SUM(route_elevation_gain_m)
            FROM analyses 
            WHERE timestamp > ? AND analysis_success = 1
        """, (cutoff,))
        total_metrics = cursor.fetchone()
        
        return {
            'total_requests': total_requests,
            'api_calls_by_day': api_calls_by_day,
            'today_http_requests': today_http_requests or 0,
            'today_estimated_cost': today_estimated_cost,
            'avg_daily_cost': avg_daily_cost,
            'avg_daily_requests': avg_daily_requests,
            'max_daily_cost': max_daily_cost,
            'max_daily_cost_date': max_daily_cost_date,
            'days_above_90_percent': days_above_90_percent,
            'days_above_75_percent': days_above_75_percent,
            'successful_analyses': successful_analyses,
            'total_analyses': total_analyses,
            'today_analyses': today_analyses,
            'avg_daily_analyses': avg_daily_analyses,
            'avg_distance_km': avg_metrics[0] if avg_metrics[0] else 0,
            'avg_elevation_m': avg_metrics[1] if avg_metrics[1] else 0,
            'total_distance_km': total_metrics[0] if total_metrics[0] else 0,
            'total_elevation_m': total_metrics[1] if total_metrics[1] else 0
        }


def get_hourly_stats():
    """Get API usage by hour for today"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT strftime('%H', timestamp) as hour, 
                   COUNT(*) as http_requests,
                   SUM(estimated_cost) as estimated_cost
            FROM api_calls 
            WHERE DATE(timestamp) = DATE('now')
            GROUP BY hour
            ORDER BY hour
        """)
        return cursor.fetchall()


def get_api_details(days=7):
    """Get detailed API usage breakdown"""
    with get_db() as conn:
        cursor = conn.cursor()
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
        
        cursor.execute("""
            SELECT call_type,
                   COUNT(*) as calls,
                   SUM(estimated_cost) as total_cost,
                   AVG(estimated_cost) as avg_cost,
                   AVG(locations) as avg_locations,
                   AVG(hourly_vars + daily_vars + minutely_15_vars) as avg_variables,
                   AVG(time_range_days) as avg_time_range
            FROM api_calls 
            WHERE timestamp > ? AND success = 1
            GROUP BY call_type
        """, (cutoff,))
        return cursor.fetchall()


def get_usage_status():
    """
    Get current API usage status for UI control
    
    Checks ALL Open-Meteo limits:
    - 600 calls/minute
    - 5,000 calls/hour
    - 10,000 calls/day
    - 300,000 calls/month
    
    Returns the MOST restrictive status
    
    Returns a dict with:
    - status: 'safe', 'warning', 'critical', or 'blocked'
    - usage_minute: estimated API cost in last minute
    - usage_hour: estimated API cost in last hour
    - usage_day: estimated API cost today
    - usage_month: estimated API cost this month
    - remaining_minute, remaining_hour, remaining_day, remaining_month
    - percentage_minute, percentage_hour, percentage_day, percentage_month
    - most_restrictive: which limit is closest to being hit
    - allow_requests: boolean, whether to allow new requests
    - message: user-friendly status message
    - color: suggested UI color ('green', 'orange', 'red')
    """
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.datetime.now()
        
        # Get usage for each time period
        
        # Last minute
        one_minute_ago = (now - datetime.timedelta(minutes=1)).isoformat()
        cursor.execute("""
            SELECT COALESCE(SUM(estimated_cost), 0)
            FROM api_calls 
            WHERE datetime(timestamp) > datetime(?)
        """, (one_minute_ago,))
        usage_minute = cursor.fetchone()[0]
        
        # Last hour
        one_hour_ago = (now - datetime.timedelta(hours=1)).isoformat()
        cursor.execute("""
            SELECT COALESCE(SUM(estimated_cost), 0)
            FROM api_calls 
            WHERE datetime(timestamp) > datetime(?)
        """, (one_hour_ago,))
        usage_hour = cursor.fetchone()[0]
        
        # Today
        cursor.execute("""
            SELECT COALESCE(SUM(estimated_cost), 0)
            FROM api_calls 
            WHERE DATE(timestamp) = DATE('now')
        """)
        usage_day = cursor.fetchone()[0]
        
        # This month
        cursor.execute("""
            SELECT COALESCE(SUM(estimated_cost), 0)
            FROM api_calls 
            WHERE strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now')
        """)
        usage_month = cursor.fetchone()[0]
    
    # Define limits
    limit_minute = 600
    limit_hour = 5000
    limit_day = 10000
    limit_month = 300000
    
    # Calculate metrics for each limit
    remaining_minute = limit_minute - usage_minute
    remaining_hour = limit_hour - usage_hour
    remaining_day = limit_day - usage_day
    remaining_month = limit_month - usage_month
    
    percentage_minute = (usage_minute / limit_minute) * 100
    percentage_hour = (usage_hour / limit_hour) * 100
    percentage_day = (usage_day / limit_day) * 100
    percentage_month = (usage_month / limit_month) * 100
    
    # Find most restrictive limit
    limits = {
        'minute': percentage_minute,
        'hour': percentage_hour,
        'day': percentage_day,
        'month': percentage_month
    }
    most_restrictive = max(limits, key=limits.get)
    max_percentage = limits[most_restrictive]
    
    # Determine status based on most restrictive limit
    if max_percentage >= 95:
        status = 'blocked'
        allow_requests = False
        
        if most_restrictive == 'minute':
            message = f"Minute limit reached ({percentage_minute:.1f}%). Wait 1 minute."
        elif most_restrictive == 'hour':
            message = f"Hourly limit reached ({percentage_hour:.1f}%). Wait until next hour."
        elif most_restrictive == 'day':
            message = f"Daily limit reached ({percentage_day:.1f}%). Resets at 00:00 UTC."
        else:
            message = f"Monthly limit reached ({percentage_month:.1f}%). Resets next month."
        
        color = 'red'
        
    elif max_percentage >= 90:
        status = 'critical'
        allow_requests = True
        
        if most_restrictive == 'minute':
            message = f"Critical: ~{remaining_minute:.0f} calls left this minute ({percentage_minute:.1f}%)"
        elif most_restrictive == 'hour':
            message = f"Critical: ~{remaining_hour:.0f} calls left this hour ({percentage_hour:.1f}%)"
        elif most_restrictive == 'day':
            message = f"Critical: ~{remaining_day:.0f} calls left today ({percentage_day:.1f}%)"
        else:
            message = f"Critical: ~{remaining_month:.0f} calls left this month ({percentage_month:.1f}%)"
        
        color = 'red'
        
    elif max_percentage >= 75:
        status = 'warning'
        allow_requests = True
        
        if most_restrictive == 'minute':
            message = f"Warning: ~{remaining_minute:.0f} calls left this minute ({percentage_minute:.1f}%)"
        elif most_restrictive == 'hour':
            message = f"Warning: ~{remaining_hour:.0f} calls left this hour ({percentage_hour:.1f}%)"
        elif most_restrictive == 'day':
            message = f"Warning: ~{remaining_day:.0f} calls left today ({percentage_day:.1f}%)"
        else:
            message = f"Warning: ~{remaining_month:.0f} calls left this month ({percentage_month:.1f}%)"
        
        color = 'orange'
        
    else:
        status = 'safe'
        allow_requests = True
        message = f"All limits safe (max usage: {max_percentage:.1f}%)"
        color = 'green'
    
    return {
        'status': status,
        'usage_minute': usage_minute,
        'usage_hour': usage_hour,
        'usage_day': usage_day,
        'usage_month': usage_month,
        'remaining_minute': remaining_minute,
        'remaining_hour': remaining_hour,
        'remaining_day': remaining_day,
        'remaining_month': remaining_month,
        'percentage_minute': percentage_minute,
        'percentage_hour': percentage_hour,
        'percentage_day': percentage_day,
        'percentage_month': percentage_month,
        'most_restrictive': most_restrictive,
        'allow_requests': allow_requests,
        'message': message,
        'color': color
    }


# Initialize database on import
init_db()
