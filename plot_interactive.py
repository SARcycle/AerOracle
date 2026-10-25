"""
Interactive route figures for the web app: a map colored by a selectable metric and an elevation
(and temperature) profile. Both figures share the point order of track.points, so a point index
hovered in one figure can be shown in the other (see dash_assets/hover_sync.js).
"""
import math

import numpy as np
import plotly.colors
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def calculate_mapbox_bounds(lats, lons, padding_percent=15, min_span=0.01):
    """
    Calculate optimal map bounds with intelligent padding and aspect ratio handling.
    
    Parameters:
    -----------
    lats : array-like
        Latitude coordinates
    lons : array-like
        Longitude coordinates
    padding_percent : float, default=15
        Percentage of range to add as padding (15% means 15% on each side)
    min_span : float, default=0.01
        Minimum span in degrees to prevent excessive zoom for point-like routes
    
    Returns:
    --------
    dict : Contains 'center' (lat, lon), 'zoom', and 'bounds' for the map
    """
    
    # Calculate basic bounds
    lat_min, lat_max = np.min(lats), np.max(lats)
    lon_min, lon_max = np.min(lons), np.max(lons)
    
    # Calculate spans
    lat_span = lat_max - lat_min
    lon_span = lon_max - lon_min
    
    # Handle edge case: all points are basically the same (< 1km apart)
    if lat_span < 0.001 and lon_span < 0.001:
        return {
            'center': {'lat': np.mean(lats), 'lon': np.mean(lons)},
            'zoom': 14,  # Good zoom for a single location
            'bounds': None
        }
    
    # Apply minimum span to prevent excessive zoom for very short routes
    lat_span = max(lat_span, min_span)
    lon_span = max(lon_span, min_span)
    
    # Calculate aspect ratio
    aspect_ratio = lon_span / lat_span if lat_span > 0 else 1
    
    # Adjust padding based on aspect ratio
    # For very elongated routes (aspect ratio > 5 or < 0.2), 
    # add MORE padding to the narrow dimension
    if aspect_ratio > 5:  # Very wide route (horizontal line)
        lat_padding_percent = padding_percent * 2  # Extra vertical padding
        lon_padding_percent = padding_percent
    elif aspect_ratio < 0.2:  # Very tall route (vertical line)
        lat_padding_percent = padding_percent
        lon_padding_percent = padding_percent * 2  # Extra horizontal padding
    else:  # Balanced route
        lat_padding_percent = padding_percent
        lon_padding_percent = padding_percent
    
    # Calculate padding in degrees
    lat_padding = lat_span * (lat_padding_percent / 100)
    lon_padding = lon_span * (lon_padding_percent / 100)
    
    # Apply padding
    lat_min_padded = lat_min - lat_padding
    lat_max_padded = lat_max + lat_padding
    lon_min_padded = lon_min - lon_padding
    lon_max_padded = lon_max + lon_padding
    
    # Calculate center
    center_lat = (lat_min_padded + lat_max_padded) / 2
    center_lon = (lon_min_padded + lon_max_padded) / 2
    
    # Calculate zoom level
    zoom = calculate_zoom_from_bounds(
        lat_min_padded, lat_max_padded,
        lon_min_padded, lon_max_padded
    )
    
    return {
        'center': {'lat': center_lat, 'lon': center_lon},
        'zoom': zoom,
        'bounds': {
            'south': lat_min_padded,
            'north': lat_max_padded,
            'west': lon_min_padded,
            'east': lon_max_padded
        }
    }


def calculate_zoom_from_bounds(lat_min, lat_max, lon_min, lon_max, 
                                map_width_px=1200, map_height_px=600):
    """
    Calculate zoom level from geographic bounds using Web Mercator projection.
    
    This accounts for:
    - Latitude-dependent distortion (Mercator projection stretches near poles)
    - Map dimensions in pixels
    - Ensures all points fit within the viewport
    """
    
    # Calculate spans
    lat_span = lat_max - lat_min
    lon_span = lon_max - lon_min
    
    # Prevent division by zero
    if lat_span == 0 or lon_span == 0:
        return 10
    
    # World dimensions at zoom level 0 (in pixels)
    WORLD_DIM = 256
    
    # Calculate zoom based on longitude
    zoom_lon = math.log2(map_width_px * 360 / (lon_span * WORLD_DIM))
    
    # Calculate zoom based on latitude (Web Mercator)
    def lat_to_mercator_y(lat_deg):
        """Convert latitude to Mercator y-coordinate (0 to 1)"""
        lat_rad = math.radians(lat_deg)
        return math.log(math.tan(math.pi / 4 + lat_rad / 2))
    
    # Calculate Mercator y-span
    y_min = lat_to_mercator_y(lat_min)
    y_max = lat_to_mercator_y(lat_max)
    y_span = abs(y_max - y_min)
    
    # Calculate zoom from latitude
    zoom_lat = math.log2(map_height_px * 2 * math.pi / (y_span * WORLD_DIM))
    
    # Use the smaller zoom to ensure everything fits
    zoom = min(zoom_lon, zoom_lat)-1
    
    # Clamp to valid Mapbox zoom range (0-22, but 20 is practical max)
    zoom = max(0, min(20, zoom))
    
    # Round down to ensure everything fits (conservative approach)
    zoom = math.floor(zoom * 10) / 10  # Round to 1 decimal place
    
    return zoom


MAP_ROUTE_TRACE = 0         # Route colored by the selected metric
MAP_CURSOR_TRACE = 1        # Marker showing the point hovered in the profile
PROFILE_ELEVATION_TRACE = 1  # 0: gradient bands, 1: elevation line, 2: outline and 3: temperature (with weather)
PROFILE_CURSOR_SHAPE = 0    # Vertical line showing the point hovered on the map

# Light/dark colors, same values as dash_assets/theme.js (which switches them live when the system setting changes).
# Figure backgrounds are transparent: the cards behind them follow the page theme.
THEMES = {
    'light': dict(font='#4a5866', line='#15202b', grid='#e3e8ed', label_bg='rgba(255,255,255,0.92)', label_border='#d6dde4',
                  map_style='open-street-map'),
    'dark': dict(font='#a9b6c3', line='#e6edf3', grid='#2a3643', label_bg='rgba(23,32,42,0.92)', label_border='#2a3643',
                 map_style='open-street-map'),  # carto-darkmatter lacks details
}
TRANSPARENT = 'rgba(0,0,0,0)'
CURSOR_COLOR = '#d6336c'
TEMPERATURE_COLOR = '#3d8fd6'
START_COLOR, FINISH_COLOR = '#1d4f91', '#15202b'
SUMMIT_SYMBOL = 'aeroracle-summit'  # Mountain icon, added to the map in the browser (hover_sync.js)

# Gradient bands of the elevation profile: (upper limit [%], color)
GRADIENT_BANDS = [(-9, '#08306b'), (-6, '#2171b5'), (-3, '#6baed6'), (3, '#9ccf8d'), (6, '#f2c12e'), (9, '#ea7d24'), (math.inf, '#c63a2f')]


def gradient_color(gradient_pct):
    return next(color for limit, color in GRADIENT_BANDS if gradient_pct < limit)


def format_elapsed_time(seconds):
    """Convert seconds into HH:MM:SS format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def route_data(parameters, assets):
    """Per-point arrays used by both figures."""
    track = assets.track
    zeros = np.zeros(len(track))
    elapsed_time = track['timestamp'] - track['timestamp'][0]
    return dict(
        weather=parameters.time is not False and track.has('temperature_c'),
        lats=track['lat'],
        lons=track['lon'],
        distance_km=track['distance_m'] / 1000,
        elevation=track['elevation_m'],
        speed_riding=track['speed_ms'] * 3.6,  # Estimated speed [km/h]
        elapsed_time=elapsed_time,
        formatted_elapsed_time=np.array([format_elapsed_time(t) for t in elapsed_time]),
        gradient=track['gradient'] * 100,  # Convert from 0-1 to 0-100%
        wind_speed=track['headwind_ms'] if track.has('headwind_ms') else zeros,
        yaw=track['yaw_angle_deg'] if track.has('yaw_angle_deg') else zeros,  # Wind direction relative to travel direction
        temperature=track['temperature_c'] if track.has('temperature_c') else zeros,
    )


def color_options(parameters, assets):
    """
    Metrics the map can be colored by: label -> marker settings (JSON-ready, for the "Color by" menu).
    Colorscales as explicit colors: plotly.js knows only a few names (no 'RdYlGn', no '_r' reversal).
    """
    d = route_data(parameters, assets)

    def option(values, colorscale, cmin, cmax, title, ends=None):
        # ends: meaning of the negative (left) and positive (right) end of the colorbar
        return dict(color=np.round(values, 2).tolist(), colorscale=plotly.colors.get_colorscale(colorscale),
                    cmin=float(cmin), cmax=float(cmax), title=title, ends=ends)

    wind_limit = np.round(np.nanmax([4, np.abs(np.percentile(d['wind_speed'], 5)), np.abs(np.percentile(d['wind_speed'], 95))]))
    options = {}
    if d['weather']:
        options['Wind'] = option(d['wind_speed'], 'RdYlGn_r', -wind_limit - 0.5, wind_limit + 0.5, "Wind [m/s]", ends=("− tail wind", "+ head wind"))
    options['Speed'] = option(d['speed_riding'], 'RdYlGn_r', 0, np.max(d['speed_riding']), "Speed [km/h]")
    if d['weather']:
        options['Yaw angle'] = option(d['yaw'], 'RdBu_r', -25, 25, "Yaw angle [°]", ends=("− from left", "+ from right"))
    options['Elevation'] = option(d['elevation'], 'Viridis', np.nanmin(d['elevation']), np.nanmax(d['elevation']), "Elevation [m]")
    if d['weather']:
        options['Temperature'] = option(d['temperature'], 'RdYlBu_r', np.nanmin(d['temperature']), np.nanmax(d['temperature']), "Temperature [°C]")
    options['Gradient'] = option(d['gradient'], 'RdBu_r', -10, 10, "Gradient [%]")
    return options


# Horizontal colorbar below the map; its end labels (colorbar_end_labels) sit on the title's line at the bar's ends
MAP_HEIGHT = 620
MAP_BOTTOM = 0.13  # Map area from this height (paper coordinates) up
COLORBAR_LENGTH, COLORBAR_THICKNESS_PX, COLORBAR_Y = 0.6, 12, 0.025
COLORBAR = dict(orientation='h', x=0.5, xanchor='center', y=COLORBAR_Y, yanchor='bottom', len=COLORBAR_LENGTH, thickness=COLORBAR_THICKNESS_PX,
                nticks=8, xpad=0)  # No padding: the bar's ends are at 0.5 ± COLORBAR_LENGTH / 2
COLORBAR_TITLE_LINE_PX = 47  # Center of the colorbar title above COLORBAR_Y (measured: padding, tick labels, bar, gap)


def colorbar_end_labels(ends):
    """Annotations above the left (negative) and right (positive) end of the colorbar; hidden without ends."""
    return [dict(text=(ends or ('', ''))[k], visible=bool(ends), x=0.5 + (k - 0.5) * COLORBAR_LENGTH, xanchor='left' if k == 0 else 'right',
                 y=COLORBAR_Y, yanchor='middle', yshift=COLORBAR_TITLE_LINE_PX, xref='paper', yref='paper', showarrow=False,
                 font=dict(size=12)) for k in (0, 1)]


def map_figure(parameters, assets, options, selected, summits=(), theme='light'):
    """Route map colored by options[selected], with start, finish and summit markers."""
    d = route_data(parameters, assets)
    colors = THEMES[theme]

    # Numbers in customdata (sent as compact binary arrays), strings in text/hovertext
    hover_lines = [
        "Elapsed Time: %{text}",
        "Distance: %{customdata[0]:.2f} km",
        "Speed: %{customdata[1]:.2f} km/h",
        "Elevation: %{customdata[2]:.0f} m",
        "Gradient: %{customdata[3]:.1f}%",
    ]
    customdata = [d['distance_km'], d['speed_riding'], d['elevation'], d['gradient']]
    hovertext = None
    if d['weather']:
        wind_label = np.where(d['wind_speed'] < 0, '(Tail wind)', np.where(d['wind_speed'] > 0, '(Head wind)', '(No wind)'))
        yaw_label = np.where(d['yaw'] < 0, '(from left)', np.where(d['yaw'] > 0, '(from right)', '(head-on)'))
        hovertext = [f"Wind: {w:.1f} m/s {wl}<br>Yaw: {y:.1f}° {yl}" for w, wl, y, yl in zip(d['wind_speed'], wind_label, d['yaw'], yaw_label)]
        hover_lines += ["%{hovertext}", "Temp: %{customdata[4]:.1f}°C"]
        customdata.append(d['temperature'])
    hover_lines.append("Coordinates: %{lat:.4f}, %{lon:.4f}")

    option = options[selected]
    fig = go.Figure()
    fig.add_trace(go.Scattermap(  # MapLibre map (plotly >= 5.24; the Mapbox traces were removed in plotly 7)
        lon=d['lons'],
        lat=d['lats'],
        mode='lines+markers',
        marker=dict(size=6, color=option['color'], colorscale=option['colorscale'], cmin=option['cmin'], cmax=option['cmax'],
                    colorbar=COLORBAR | dict(title=dict(text=option['title'], side='top', font=dict(size=13))), showscale=True),
        line=dict(width=3, color='rgba(100,100,100,0.3)'),
        customdata=np.column_stack(customdata),
        text=d['formatted_elapsed_time'],
        hovertext=hovertext,
        hovertemplate='<br>'.join(hover_lines) + '<extra></extra>',
        name='Route'
    ))
    fig.add_trace(go.Scattermap(  # Cursor, moved by hover_sync.js
        lon=[None], lat=[None], mode='markers', marker=dict(size=16, color=CURSOR_COLOR, opacity=0.9), hoverinfo='skip', name='Cursor'))
    # Markers without map text: text labels would load fonts from a third-party glyph server
    fig.add_trace(go.Scattermap(
        lon=[d['lons'][0], d['lons'][-1]], lat=[d['lats'][0], d['lats'][-1]], mode='markers',
        marker=dict(size=15, color=[START_COLOR, FINISH_COLOR]), hovertext=['Start', 'Finish'], hoverinfo='text', name='Start/finish'))
    if summits:
        fig.add_trace(go.Scattermap(
            lon=[d['lons'][s['index']] for s in summits], lat=[d['lats'][s['index']] for s in summits], mode='markers',
            marker=dict(size=10, symbol=SUMMIT_SYMBOL, allowoverlap=True), hoverinfo='text', name='Summits',
            hovertext=[f"Summit {s['top_m']:.0f} m · km {s['top_km']:.1f}" for s in summits]))

    bounds = calculate_mapbox_bounds(d['lats'], d['lons'], padding_percent=15)
    fig.update_layout(
        map=dict(style=colors['map_style'], center=bounds['center'], zoom=bounds['zoom'], domain=dict(x=[0, 1], y=[MAP_BOTTOM, 1])),
        height=MAP_HEIGHT,
        margin=dict(l=0, r=0, t=0, b=0),
        annotations=colorbar_end_labels(option['ends']),
        paper_bgcolor=TRANSPARENT,
        font=dict(color=colors['font']),
        showlegend=False,
        hovermode='closest',
    )
    return fig


def profile_figure(parameters, assets, summits=(), theme='light'):
    """Elevation profile filled by gradient band (up and down), with the temperature on the right axis (with weather)."""
    d = route_data(parameters, assets)
    colors = THEMES[theme]
    km, elevation, gradient = d['distance_km'], d['elevation'], d['gradient']
    floor = np.floor((np.nanmin(elevation) - 100) / 250) * 250
    top = np.ceil((np.nanmax(elevation) + 150) / 250) * 250

    fig = make_subplots(specs=[[{"secondary_y": d['weather']}]])

    # Gradient bands as bars, one per segment of at most ~1000 segments
    edges = np.unique(np.linspace(0, len(km) - 1, min(len(km), 1001)).round().astype(int))
    starts, ends = edges[:-1], edges[1:]
    segment_gradient = np.array([np.nanmean(gradient[a:b + 1]) for a, b in zip(starts, ends)])
    fig.add_trace(go.Bar(
        x=(km[starts] + km[ends]) / 2, width=km[ends] - km[starts],
        y=(elevation[starts] + elevation[ends]) / 2 - floor, base=floor,
        marker=dict(color=[gradient_color(g) for g in segment_gradient], line=dict(width=0)),
        hoverinfo='skip', showlegend=False,
    ), secondary_y=False)

    fig.add_trace(go.Scatter(
        x=km,
        y=elevation,
        mode='lines',
        line=dict(color=colors['line'], width=1.6),
        name='Elevation',
        customdata=np.column_stack([d['lats'], d['lons'], gradient]),
        text=d['formatted_elapsed_time'],
        hovertemplate='Elevation: %{y:.0f} m<br>Gradient: %{customdata[2]:.1f}%<br>Elapsed time: %{text}<extra></extra>',
    ), secondary_y=False)

    if d['weather']:
        fig.add_trace(go.Scatter(  # Thin white outline under the temperature line: readable over the gradient bands
            x=km, y=d['temperature'], mode='lines', line=dict(color='rgba(255,255,255,0.9)', width=3.5),
            hoverinfo='skip', showlegend=False,
        ), secondary_y=True)
        fig.add_trace(go.Scatter(
            x=km,
            y=d['temperature'],
            mode='lines',
            line=dict(color=TEMPERATURE_COLOR, width=2),
            name='Temperature',
            hovertemplate='Temperature: %{y:.1f}°C<extra></extra>',
        ), secondary_y=True)
        t_min, t_max = np.nanmin(d['temperature']), np.nanmax(d['temperature'])
        span = max(t_max - t_min, 4)
        fig.update_yaxes(title_text="Temperature [°C]", showgrid=False, secondary_y=True, range=[t_min - span * 0.15, t_max + span * 0.35],
                         dtick=5 if span > 8 else 2, tickformat='.0f', title_font=dict(color=TEMPERATURE_COLOR), tickfont=dict(color=TEMPERATURE_COLOR))

    label_shifts = summit_label_shifts(summits, km[-1], floor, top)
    fig.update_yaxes(title_text="Elevation [m]", range=[floor, top], gridcolor=colors['grid'], zeroline=False, secondary_y=False)
    fig.update_xaxes(title_text="Distance [km]", showgrid=False, range=[km[0], km[-1]],
                     unifiedhovertitle=dict(text='Distance: %{x:.1f} km'))  # Title line of the hover box
    fig.update_layout(
        height=320,
        # No side margins: the axes reserve exactly the space their numbers and titles need (automargin)
        margin=dict(l=0, r=0, t=24 + max([0, *(shift - 11 for shift in label_shifts)]), b=50),
        paper_bgcolor=TRANSPARENT,
        plot_bgcolor=TRANSPARENT,
        font=dict(color=colors['font']),
        showlegend=False,
        bargap=0,
        hovermode='x unified',  # Hover anywhere above a distance
        # Opaque labels: with the transparent figure background, the hover box would be transparent over the gradient bands
        hoverlabel=dict(bgcolor=colors['label_bg'], bordercolor=colors['label_border'], font=dict(color=colors['line'])),
        shapes=[dict(  # Cursor, moved by hover_sync.js
            type='line', xref='x', yref='paper', x0=0, x1=0, y0=0, y1=1,
            line=dict(color=CURSOR_COLOR, width=2), visible=False
        )],
        annotations=[dict(x=s['top_km'], y=s['top_m'], text=f"{s['top_m']:.0f} m", showarrow=False, yshift=shift,
                          # Labels near the ends align inwards, clear of the axes
                          xanchor='right' if s['top_km'] > 0.95 * km[-1] else 'left' if s['top_km'] < 0.05 * km[-1] else 'center',
                          bgcolor=colors['label_bg'], borderpad=2, font=dict(size=11, color=colors['line']))
                     for s, shift in zip(summits, label_shifts)],
    )
    return fig


def summit_label_shifts(summits, total_km, floor, top, plot_width_px=700, plot_height_px=230):
    """
    Vertical offsets [px] of the summit labels: right above the summit, moved up in steps while the label's box would
    overlap one placed before (positions estimated for a plot of plot_width_px x plot_height_px; narrow plots are the worst case).
    """
    placed, shifts = [], []
    for s in summits:
        x = s['top_km'] / total_km * plot_width_px
        base = (s['top_m'] - floor) / (top - floor) * plot_height_px
        width = len(f"{s['top_m']:.0f} m") * 6.5 + 8
        shift = 11
        while any(abs(x - px) < (width + pw) / 2 and abs(base + shift - py) < 16 for px, py, pw in placed):
            shift += 16
        placed.append((x, base + shift, width))
        shifts.append(shift)
    return shifts


def gradient_scale():
    """Colors of the profile's gradient bands and the gradients [%] at the boundaries between them."""
    return [color for _, color in GRADIENT_BANDS], [limit for limit, _ in GRADIENT_BANDS[:-1]]
