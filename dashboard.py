"""Usage statistics of AerOracle (usage.db): weather API calls with cost estimates, analyses, resource use."""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

import tracker
from tracker import get_db

st.set_page_config(
    page_title="AerOracle Usage Dashboard",
    page_icon=".streamlit/favicon.ico",
    layout="wide",
    menu_items={
        'Get Help': None,  # Remove the default help menu
        'Report a bug': None,  # Remove bug report
        'About': ""# AerOracle
    }
)

st.title("AerOracle Usage Dashboard")
st.markdown("*API usage monitoring with cost estimation*")

# Time range selector
st.sidebar.header("Settings")
days = st.sidebar.selectbox(
    "Time Range",
    [1, 7, 14, 30, 90, 9999],
    index=1,
    format_func=lambda x: f"Last {x} day{'s' if x > 1 else ''}"
)

# Auto-refresh option
auto_refresh = st.sidebar.checkbox("Auto-refresh (30s)", value=False)
if auto_refresh:
    import time
    time.sleep(30)
    st.rerun()

# Refresh button
if st.sidebar.button("Refresh Now"):
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.info("""
**Note on API Costs:**  
Open-Meteo doesn't provide actual usage data.  
These are *estimates* based on request parameters.  
The real cost may vary.
""")

# Get stats
stats = tracker.get_stats(days=days)
hourly_stats = tracker.get_hourly_stats()
api_details = tracker.get_api_details(days=days)

# Store days for resource stats
days_back = days

# --- Main Metrics ---
st.header("Overview")

# First row: HTTP Requests
st.subheader("HTTP Requests")
col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Total Requests",
        f"{stats['total_requests']:,}",
        help=f"Total HTTP requests in the last {days} day(s)"
    )

with col2:
    st.metric(
        "Today's Requests",
        f"{stats['today_http_requests']:,}",
        help="HTTP requests made today"
    )

with col3:
    st.metric(
        "Avg Daily Requests",
        f"{stats['avg_daily_requests']:.1f}",
        help=f"Average HTTP requests per day (last {days} days)"
    )

# Second row: Analyses
st.subheader("Route Analyses")
col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Total Analyses",
        f"{stats['successful_analyses']:,}",
        help=f"Successful route analyses in the last {days} day(s)"
    )

with col2:
    st.metric(
        "Today's Analyses",
        f"{stats['today_analyses']:,}",
        help="Successful analyses today"
    )

with col3:
    st.metric(
        "Avg Daily Analyses",
        f"{stats['avg_daily_analyses']:.1f}",
        help=f"Average analyses per day (last {days} days)"
    )

# Third row: API Cost
st.subheader("API Cost")
col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Today's Estimated Cost",
        f"{stats['today_estimated_cost']:.1f}",
        help="Estimated API cost today (Open-Meteo formula)"
    )

with col2:
    st.metric(
        "Avg Daily Cost",
        f"{stats['avg_daily_cost']:.1f}",
        help=f"Average estimated API cost per day (last {days} days)"
    )

with col3:
    # Show remaining today
    remaining_today = 10000 - stats['today_estimated_cost']
    percentage_today = (stats['today_estimated_cost'] / 10000) * 100
    st.metric(
        "Remaining Today",
        f"{remaining_today:.0f}",
        delta=f"{percentage_today:.1f}% used",
        delta_color="inverse",
        help="Estimated calls remaining today (10k daily limit)"
    )

# --- Historical Statistics ---
st.subheader("Historical Stats (All Time)")
col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Peak Daily Cost",
        f"{stats['max_daily_cost']:.1f}",
        help="Highest estimated daily API cost ever recorded"
    )
    if stats['max_daily_cost_date']:
        st.caption(f"Occurred on: {stats['max_daily_cost_date']}")

with col2:
    st.metric(
        "Days >90% Limit",
        f"{stats['days_above_90_percent']}",
        help="Number of days that exceeded 9,000 estimated API calls (90% of 10k limit)"
    )
    if stats['days_above_90_percent'] > 0:
        st.caption("You've been close to the limit!")

with col3:
    st.metric(
        "Days >75% Limit",
        f"{stats['days_above_75_percent']}",
        help="Number of days that exceeded 7,500 estimated API calls (75% of 10k limit)"
    )
    if stats['days_above_75_percent'] > 5:
        st.caption("Monitor usage closely")

# --- API Usage Warning ---
st.header("API Usage Status - All Limits")

col1, col2 = st.columns([1, 2])

with col1:
    # Get current status
    status = tracker.get_usage_status()
    
    # Show all 4 limits
    st.metric("Last Minute", f"{status['usage_minute']:.0f} / 600")
    st.metric("Last Hour", f"{status['usage_hour']:.0f} / 5,000")
    st.metric("Today", f"{stats['today_estimated_cost']:.0f} / 10,000")
    st.metric("This Month", f"{status['usage_month']:.0f} / 300,000")

with col2:
    # Most restrictive limit
    st.markdown(f"**Most Restrictive Limit:** {status['most_restrictive'].title()}")
    
    # Status message
    if status['status'] == 'blocked':
        st.error(status['message'])
    elif status['status'] == 'critical':
        st.error(status['message'])
    elif status['status'] == 'warning':
        st.warning(status['message'])
    else:
        st.success(status['message'])
    
    # Progress bars for all limits
    st.markdown("**Minute Limit (600 calls)**")
    progress_minute = min(status['percentage_minute'] / 100, 1.0)
    st.progress(progress_minute)
    st.caption(f"{status['percentage_minute']:.1f}% used - {status['remaining_minute']:.0f} calls remaining")
    
    st.markdown("**Hour Limit (5,000 calls)**")
    progress_hour = min(status['percentage_hour'] / 100, 1.0)
    st.progress(progress_hour)
    st.caption(f"{status['percentage_hour']:.1f}% used - {status['remaining_hour']:.0f} calls remaining")
    
    st.markdown("**Day Limit (10,000 calls)**")
    progress_day = min(status['percentage_day'] / 100, 1.0)
    st.progress(progress_day)
    st.caption(f"{status['percentage_day']:.1f}% used - {status['remaining_day']:.0f} calls remaining")
    
    st.markdown("**Month Limit (300,000 calls)**")
    progress_month = min(status['percentage_month'] / 100, 1.0)
    st.progress(progress_month)
    st.caption(f"{status['percentage_month']:.1f}% used - {status['remaining_month']:.0f} calls remaining")
    
    # Comparison to average and peak (daily)
    if stats['avg_daily_cost'] > 0:
        st.caption(f"Today vs Average: {(stats['today_estimated_cost'] / stats['avg_daily_cost'] * 100 - 100):+.0f}%")
    if stats['max_daily_cost'] > 0:
        st.caption(f"Today vs Peak: {(stats['today_estimated_cost'] / stats['max_daily_cost'] * 100):.0f}%")

# --- Hourly Usage Today ---
if hourly_stats:
    st.subheader("Today's Hourly Usage")
    
    hours = [int(h) for h, _, _ in hourly_stats]
    http_requests = [r for _, r, _ in hourly_stats]
    estimated_costs = [c for _, _, c in hourly_stats]
    
    # Create subplot with two y-axes
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    
    fig.add_trace(
        go.Bar(
            x=hours,
            y=http_requests,
            name='HTTP Requests',
            marker_color='rgb(55, 83, 109)',
            text=http_requests,
            textposition='auto'
        ),
        secondary_y=False
    )
    
    fig.add_trace(
        go.Scatter(
            x=hours,
            y=estimated_costs,
            name='Estimated Cost',
            line=dict(color='rgb(255, 127, 14)', width=3),
            mode='lines+markers'
        ),
        secondary_y=True
    )
    
    fig.update_xaxes(title_text="Hour of Day (UTC)", tickmode='linear', tick0=0, dtick=1)
    fig.update_yaxes(title_text="HTTP Requests", secondary_y=False)
    fig.update_yaxes(title_text="Estimated API Cost", secondary_y=True)
    
    fig.update_layout(
        height=350,
        margin=dict(l=20, r=20, t=20, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode='x unified'
    )
    
    st.plotly_chart(fig, width='stretch')
    
    # Hourly limit warning
    max_hourly = max(estimated_costs) if estimated_costs else 0
    if max_hourly > 4000:
        st.error(f"Peak hour: {max_hourly:.0f} estimated cost (hourly limit: 5,000)")
    elif max_hourly > 3000:
        st.warning(f"Peak hour: {max_hourly:.0f} estimated cost")

# --- API Calls by Day ---
st.subheader(f"API Usage Over Last {days} Days")

if stats['api_calls_by_day']:
    df = pd.DataFrame(
        stats['api_calls_by_day'],
        columns=['Date', 'HTTP_Requests', 'Estimated_Cost', 'Successful']
    )
    
    # Create subplot with two y-axes
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    
    fig.add_trace(
        go.Bar(
            x=df['Date'],
            y=df['HTTP_Requests'],
            name='HTTP Requests',
            marker_color='rgb(158, 202, 225)',
            opacity=0.7
        ),
        secondary_y=False
    )
    
    fig.add_trace(
        go.Bar(
            x=df['Date'],
            y=df['Estimated_Cost'],
            name='Estimated API Cost',
            marker_color='rgb(255, 127, 14)',
            opacity=0.8
        ),
        secondary_y=True
    )
    
    # Add threshold lines
    fig.add_trace(
        go.Scatter(
            x=df['Date'],
            y=[10000] * len(df),
            name='Daily Limit (10k)',
            line=dict(color='red', dash='dash', width=2),
            mode='lines'
        ),
        secondary_y=True
    )
    
    fig.add_trace(
        go.Scatter(
            x=df['Date'],
            y=[7500] * len(df),
            name='75% Threshold',
            line=dict(color='orange', dash='dot', width=1),
            mode='lines',
            opacity=0.5
        ),
        secondary_y=True
    )
    
    # Add average line
    if stats['avg_daily_cost'] > 0:
        fig.add_trace(
            go.Scatter(
                x=df['Date'],
                y=[stats['avg_daily_cost']] * len(df),
                name=f'Average ({stats["avg_daily_cost"]:.0f})',
                line=dict(color='green', dash='dash', width=1),
                mode='lines',
                opacity=0.5
            ),
            secondary_y=True
        )
    
    fig.update_xaxes(title_text="Date")
    fig.update_yaxes(title_text="HTTP Requests", secondary_y=False)
    fig.update_yaxes(title_text="Estimated API Cost", secondary_y=True)
    
    fig.update_layout(
        height=450,
        margin=dict(l=20, r=20, t=20, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode='x unified',
        barmode='group'
    )
    
    st.plotly_chart(fig, width='stretch')
    
    # Show data table
    with st.expander("View Raw Data"):
        df_display = df.copy()
        df_display['Date'] = pd.to_datetime(df_display['Date']).dt.date
        df_display['% of Limit'] = (df_display['Estimated_Cost'] / 10000 * 100).round(1)
        st.write("**Daily Summary:**")
        st.dataframe(df_display, width='stretch')
        
        st.write("**Individual API Calls:**")
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, locations, hourly_vars, daily_vars, 
                       minutely_15_vars, estimated_cost, success
                FROM api_calls
                WHERE timestamp > ?
                ORDER BY timestamp DESC
                LIMIT 100
            """, ((datetime.now() - timedelta(days=days)).isoformat(),))
            calls = cursor.fetchall()
            
        if calls:
            calls_df = pd.DataFrame(
                calls,
                columns=['Timestamp', 'Locations', 'Hourly', 'Daily', 'Minutely', 'Cost', 'Success']
            )
            st.dataframe(calls_df, width='stretch')
        else:
            st.info("No individual calls to show")
else:
    st.info("No API calls recorded in this time period")

# --- API Call Details ---
if api_details:
    st.subheader("API Call Breakdown")
    
    detail_df = pd.DataFrame(
        api_details,
        columns=['Type', 'Calls', 'Total_Cost', 'Avg_Cost', 
                'Avg_Locations', 'Avg_Variables', 'Avg_Time_Range']
    )
    
    col1, col2 = st.columns(2)
    
    with col1:
        # Pie chart of costs by type
        fig = go.Figure(data=[go.Pie(
            labels=detail_df['Type'],
            values=detail_df['Total_Cost'],
            hole=.3
        )])
        fig.update_layout(
            title="Cost Distribution by Call Type",
            height=300,
            margin=dict(l=20, r=20, t=40, b=20)
        )
        st.plotly_chart(fig, width='stretch')
    
    with col2:
        st.markdown("**Average Parameters per Call Type**")
        params_df = detail_df[['Type', 'Avg_Locations', 'Avg_Variables', 'Avg_Time_Range']].copy()
        params_df['Avg_Locations'] = params_df['Avg_Locations'].round(1)
        params_df['Avg_Variables'] = params_df['Avg_Variables'].round(1)
        params_df['Avg_Time_Range'] = params_df['Avg_Time_Range'].round(1)
        st.dataframe(params_df, width='stretch', hide_index=True)

# --- Route Statistics ---
st.header("Route Analysis Statistics")
col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Avg Distance",
        f"{stats['avg_distance_km']:.1f} km" if stats['avg_distance_km'] > 0 else "N/A",
        help="Average route distance analyzed"
    )

with col2:
    st.metric(
        "Avg Elevation",
        f"{stats['avg_elevation_m']:.0f} m" if stats['avg_elevation_m'] > 0 else "N/A",
        help="Average elevation gain"
    )

with col3:
    if stats.get('total_analyses', 0) > 0:
        success_rate = (stats['successful_analyses'] / stats['total_analyses']) * 100
        st.metric(
            "Success Rate",
            f"{success_rate:.1f}%",
            help="Percentage of route analyses that completed successfully"
        )
    else:
        st.metric("Success Rate", "N/A", help="No analyses yet")

# Totals
col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Total Distance",
        f"{stats['total_distance_km']:.1f} km" if stats.get('total_distance_km', 0) > 0 else "N/A",
        help=f"Total distance across all {stats.get('successful_analyses', 0)} successful analyses"
    )

with col2:
    st.metric(
        "Total Elevation",
        f"{stats['total_elevation_m']:.0f} m" if stats.get('total_elevation_m', 0) > 0 else "N/A",
        help=f"Total elevation gain across all analyses"
    )

with col3:
    st.metric(
        "Total Analyses",
        f"{stats.get('successful_analyses', 0):,}",
        help="Number of successfully completed route analyses"
    )

st.markdown("---")

# --- Resource Usage Monitoring ---
st.header("Resource Usage - CPU & RAM")

try:
    from tracker_with_resources import get_resource_stats, get_recent_operations
    
    # Get resource stats for selected period
    resource_stats = get_resource_stats(days=days_back)
    
    if resource_stats and resource_stats['operations'] > 0:
        col1, col2 = st.columns([1, 2])
        
        with col1:
            st.subheader("Peak Resources")
            
            # Peak CPU
            st.metric("Peak CPU", f"{resource_stats['peak_cpu']:.1f}%", help="Highest CPU during any operation")
            
            # Avg Peak CPU
            st.metric("Avg Peak CPU", f"{resource_stats['avg_peak_cpu']:.1f}%", help="Average of peak CPUs across operations")
            
            # Peak RAM
            st.metric("Peak RAM", f"{resource_stats['peak_memory_mb']:.0f} MB", 
                     delta=f"{resource_stats['peak_memory_percent']:.1f}% of system",
                     help="Highest RAM during any operation")
            
            # Avg Peak RAM
            st.metric("Avg Peak RAM", f"{resource_stats['avg_peak_memory_mb']:.0f} MB", 
                     help="Average of peak RAM across operations")
        
        with col2:
            st.subheader("Bottleneck Analysis")
            
            # Bottleneck summary
            total_ops = resource_stats['operations']
            cpu_bottlenecks = resource_stats['cpu_bottlenecks']
            mem_bottlenecks = resource_stats['memory_bottlenecks']
            bottleneck_pct = resource_stats['bottleneck_percent']
            
            # Status message
            if bottleneck_pct > 20:
                st.error(f"High bottleneck rate: {bottleneck_pct:.1f}%")
                st.caption("Frequent resource constraints - optimization recommended")
            elif bottleneck_pct > 10:
                st.warning(f"Moderate bottleneck rate: {bottleneck_pct:.1f}%")
                st.caption("Monitor closely - some operations hitting limits")
            else:
                st.success(f"Low bottleneck rate: {bottleneck_pct:.1f}%")
                st.caption("Resources are generally adequate")
            
            # Detailed breakdown
            col_a, col_b = st.columns(2)
            with col_a:
                st.metric("CPU Bottlenecks", f"{cpu_bottlenecks}", 
                         delta=f"{(cpu_bottlenecks/total_ops*100):.1f}% of ops" if total_ops > 0 else "0%",
                         help="Operations where CPU exceeded 80%")
            with col_b:
                st.metric("RAM Bottlenecks", f"{mem_bottlenecks}",
                         delta=f"{(mem_bottlenecks/total_ops*100):.1f}% of ops" if total_ops > 0 else "0%",
                         help="Operations where RAM exceeded 80% of system")
            
            # Resource thresholds guide
            with st.expander("Understanding Resource Metrics"):
                st.markdown("""
                **CPU Usage Levels:**
                - < 60%: Normal operation
                - 60-80%: Heavy usage, monitor
                - 80-95%: Bottleneck, operations slow
                - > 95%: Severe bottleneck
                
                **RAM Usage Levels:**
                - < 60% of system: Normal
                - 60-80% of system: Monitor
                - 80-95% of system: Bottleneck
                - > 95% of system: Critical
                
                **What matters:** Peak usage during operations, not idle averages.
                """)
        
        # Recent operations with resources
        st.subheader("Recent Operations with Resources")
        
        recent_ops = get_recent_operations(limit=10)
        if recent_ops:
            df_ops = pd.DataFrame(recent_ops, columns=[
                'Timestamp', 'Type', 'Duration (s)', 'Peak CPU (%)', 
                'Avg CPU (%)', 'Peak RAM (MB)', 'Avg RAM (MB)', 'RAM %'
            ])
            
            # Add bottleneck indicator column
            df_ops['Bottleneck'] = df_ops.apply(
                lambda row: 'yes' if row['Peak CPU (%)'] > 80 or row['RAM %'] > 80 else '',
                axis=1
            )
            
            # Format values
            df_ops['Duration (s)'] = df_ops['Duration (s)'].round(2)
            df_ops['Peak CPU (%)'] = df_ops['Peak CPU (%)'].round(1)
            df_ops['Avg CPU (%)'] = df_ops['Avg CPU (%)'].round(1)
            df_ops['Peak RAM (MB)'] = df_ops['Peak RAM (MB)'].round(0)
            df_ops['Avg RAM (MB)'] = df_ops['Avg RAM (MB)'].round(0)
            df_ops['RAM %'] = df_ops['RAM %'].round(1)
            
            # Display table
            st.dataframe(df_ops, width='stretch')
        else:
            st.info("No resource data for this period.")
    
    else:
        st.info("No resource data recorded yet. It is logged with each weather API call when the monitoring extra (psutil) is installed.")

except ImportError:
    st.info("Resource monitoring needs the monitoring extra (psutil): uv sync --extra monitoring")

# --- Footer ---
st.markdown("---")
col1, col2 = st.columns(2)
with col1:
    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
with col2:
    st.caption("Tip: Enable auto-refresh in the sidebar for real-time monitoring")

st.caption("API cost estimates are approximations. Open-Meteo's actual counting may differ.")
