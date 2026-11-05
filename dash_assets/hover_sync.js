// Synchronized hover between the route map and the lower plot, the elevation profile or the rider power (see plot_interactive.py):
// - hovering the lower plot moves a marker to the corresponding location on the map
// - hovering the route on the map moves a vertical line to the corresponding distance in the lower plot
// Runs entirely in the browser (Plotly.restyle / relayout), without server round trips.

(function () {
    const MAP_ROUTE_TRACE = 0;
    const MAP_CURSOR_TRACE = 1;
    const PROFILE_ELEVATION_TRACE = 1;
    const POWER_ID = 'route-power';  // Rider power: its elevation trace is the only one with customdata [lat, lon]
    const POWER_BINS = 200;          // Rider power: about this many bins across the visible distance

    // Rider power (plot_interactive.rider_power_figure): fills the stacked areas from the fine energy grid in
    // layout.meta.riderPower (speedcalculation.rider_power_grid), grouped so that about POWER_BINS bins span the visible
    // range [km0, km1] (whole route if null). Per group: the signed energies are netted (a dip or a tail wind reduces the
    // sinks), the rider's energy is split over the remaining sinks in proportion, plus the drivetrain losses.
    function renderRiderPower(plotDiv, range) {
        const grid = plotDiv && plotDiv.layout && plotDiv.layout.meta && plotDiv.layout.meta.riderPower;
        if (!grid || !plotDiv.data) {
            return;
        }
        const count = grid.rider.length;
        const span = range ? Math.abs(range[1] - range[0]) : count * grid.step_km;
        const size = Math.max(1, Math.round(span / POWER_BINS / grid.step_km));  // Fine bins per group
        const sinks = grid.names.filter((name) => name !== 'Drivetrain');
        const windy = sinks.includes('Head wind');
        const x = [];
        const power = Object.fromEntries(grid.names.map((name) => [name, []]));
        const percent = Object.fromEntries(grid.names.map((name) => [name, []]));  // Share of the bin's pedalled power (hover)
        for (let start = 0; start < count; start += size) {
            const end = Math.min(start + size, count);
            let rider = 0, time = 0;
            const net = Object.fromEntries(sinks.map((name) => [name, 0]));
            for (let i = start; i < end; i++) {
                rider += grid.rider[i];
                time += grid.time[i];
                sinks.forEach((name) => { net[name] += grid.sinks[name][i]; });
            }
            const used = {};
            if (windy) {  // Tail wind reduces the still-air drag; head wind is a sink of its own
                used['Air'] = net['Head wind'] >= 0 ? net['Air'] : Math.max(net['Air'] + net['Head wind'], 0);
                used['Head wind'] = Math.max(net['Head wind'], 0);
            } else {
                used['Air'] = Math.max(net['Air'], 0);
            }
            ['Rolling', 'Climbing', 'Accelerating'].forEach((name) => { used[name] = Math.max(net[name], 0); });
            const total = sinks.reduce((sum, name) => sum + used[name], 0);
            const share = total > 0 ? rider / total : 0;
            sinks.forEach((name) => { used[name] *= share; });
            used['Drivetrain'] = rider * (1 / grid.efficiency - 1);
            x.push(grid.start_km + (start + end) / 2 * grid.step_km);
            const pedalled = grid.names.reduce((sum, name) => sum + used[name], 0);
            grid.names.forEach((name) => {
                power[name].push(time > 0 ? Math.round(used[name] / time * 10) / 10 : 0);
                percent[name].push(pedalled > 0 ? 100 * used[name] / pedalled : 0);
            });
        }
        const update = {x: grid.names.map(() => x), y: grid.names.map((name) => power[name]), customdata: grid.names.map((name) => percent[name])};
        Plotly.restyle(plotDiv, update, grid.names.map((_, i) => i));
    }

    function plotlyDiv(id) {
        const container = document.getElementById(id);
        return container && container.querySelector('.js-plotly-plot');
    }

    // Mountain icon for the summit markers (plot_interactive.SUMMIT_SYMBOL): the OpenStreetMap style has no icons of its own
    const SUMMIT_SYMBOL = 'aeroracle-summit';
    const SUMMIT_ICON = 'data:image/svg+xml;utf8,' + encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">' +
        '<path d="M5 39 L18 20 L24 27 L31 9 L43 39 Z" fill="none" stroke="#ffffff" stroke-width="6" stroke-linejoin="round"/>' +
        '<path d="M5 39 L18 20 L24 27 L31 9 L43 39 Z" fill="#2b2118" stroke="#2b2118" stroke-width="2" stroke-linejoin="round"/></svg>');

    function addSummitIcon(mapDiv) {
        const layout = mapDiv._fullLayout && mapDiv._fullLayout.map;
        const map = layout && layout._subplot && layout._subplot.map;
        if (!map || map._aeroracleSummitIcon) {
            return;
        }
        map._aeroracleSummitIcon = true;
        const image = new Image(48, 48);
        const add = () => {
            if (!map.hasImage(SUMMIT_SYMBOL)) {
                map.addImage(SUMMIT_SYMBOL, image, {pixelRatio: 2});
            }
            map.triggerRepaint();
        };
        image.onload = add;
        image.src = SUMMIT_ICON;
        map.on('styleimagemissing', (event) => {  // E.g. after a style reload
            if (event.id === SUMMIT_SYMBOL && image.complete) {
                add();
            }
        });
    }

    function mapLibre(mapDiv) {
        const layout = mapDiv._fullLayout && mapDiv._fullLayout.map;
        return layout && layout._subplot && layout._subplot.map;
    }

    // Map cursor as a MapLibre layer of its own: moving it does not make Plotly redraw the route (thousands of points)
    const CURSOR_SOURCE = 'aeroracle-cursor';
    const NO_CURSOR = {type: 'FeatureCollection', features: []};

    function setMapCursor(mapDiv, lat, lon) {
        const map = mapLibre(mapDiv);
        if (!map) {
            return;
        }
        let source = map.getSource(CURSOR_SOURCE);
        if (!source) {  // Created on first use, again after a new figure or a style reload
            if (lat === null || !map.isStyleLoaded()) {
                return;
            }
            const cursorTrace = mapDiv._fullData && mapDiv._fullData[MAP_CURSOR_TRACE];
            const color = (cursorTrace && cursorTrace.marker && cursorTrace.marker.color) || '#000000';
            map.addSource(CURSOR_SOURCE, {type: 'geojson', data: NO_CURSOR});
            map.addLayer({id: CURSOR_SOURCE, type: 'circle', source: CURSOR_SOURCE,
                          paint: {'circle-radius': 8, 'circle-color': color, 'circle-opacity': 0.9}});
            source = map.getSource(CURSOR_SOURCE);
        }
        source.setData(lat === null ? NO_CURSOR : {type: 'Feature', properties: {}, geometry: {type: 'Point', coordinates: [lon, lat]}});
    }

    // At most one update per screen frame, with the latest hover position; nothing if it did not change
    function perFrame(update) {
        let pending = null;
        let last;
        let scheduled = false;
        return (value) => {
            pending = value;
            if (!scheduled) {
                scheduled = true;
                requestAnimationFrame(() => {
                    scheduled = false;
                    const key = JSON.stringify(pending);
                    if (key !== last) {
                        last = key;
                        update(pending);
                    }
                });
            }
        };
    }

    // Route readability on the OpenStreetMap background (also for light colors such as the flat gradient band):
    // base map with less color, and a white casing under the route
    const BASE_MAP_SATURATION = -0.6;
    const CASING = 'aeroracle-casing';

    function styleMap(mapDiv) {
        const map = mapLibre(mapDiv);
        const route = mapDiv._fullData && mapDiv._fullData[MAP_ROUTE_TRACE];
        if (!map || !route) {
            return;
        }
        const apply = () => {
            const layers = map.getStyle().layers;
            layers.filter((layer) => layer.type === 'raster').forEach((layer) => map.setPaintProperty(layer.id, 'raster-saturation', BASE_MAP_SATURATION));
            const line = {type: 'Feature', properties: {}, geometry: {type: 'LineString', coordinates: Array.from(route.lon, (lon, i) => [lon, route.lat[i]])}};
            if (map.getSource(CASING)) {
                map.getSource(CASING).setData(line);  // New route in the same map
            } else {
                const firstTrace = layers.find((layer) => layer.id.startsWith('plotly-trace-layer'));
                map.addSource(CASING, {type: 'geojson', data: line});
                map.addLayer({id: CASING, type: 'line', source: CASING, layout: {'line-join': 'round', 'line-cap': 'round'},
                              paint: {'line-color': '#ffffff', 'line-width': 9}}, firstTrace && firstTrace.id);  // Below the route
            }
        };
        if (map.isStyleLoaded()) {
            apply();
        } else {
            map.once('idle', apply);
        }
    }

    // Map extent covering the route between km0 and km1 (whole route if km0 is null); customdata[0] of the route is the distance
    function fitMapToDistance(mapDiv, km0, km1) {
        const map = mapLibre(mapDiv);
        const route = mapDiv._fullData && mapDiv._fullData[MAP_ROUTE_TRACE];
        if (!map || !route) {
            return;
        }
        let west = Infinity, south = Infinity, east = -Infinity, north = -Infinity;
        for (let i = 0; i < route.lat.length; i++) {
            const distance = route.customdata[i][0];
            if (km0 === null || (distance >= km0 && distance <= km1)) {
                west = Math.min(west, route.lon[i]); east = Math.max(east, route.lon[i]);
                south = Math.min(south, route.lat[i]); north = Math.max(north, route.lat[i]);
            }
        }
        if (west <= east) {
            map.fitBounds([[west, south], [east, north]], {padding: 40, maxZoom: 15, duration: 600});
        }
    }

    // Click on a row of the climbs table: zoom the profile (and so the map) to the climb
    document.addEventListener('click', (event) => {
        const row = event.target.closest && event.target.closest('.ao-climb-row');
        const profileDiv = plotlyDiv('route-profile');
        if (!row || !profileDiv) {
            return;
        }
        const start = Number(row.dataset.start), end = Number(row.dataset.end);
        const margin = Math.max(0.5, 0.1 * (end - start));
        profileDiv._aeroracleClimbZoom = true;  // Keeps the row marked (any other zoom clears it)
        Plotly.relayout(profileDiv, {'xaxis.range': [start - margin, end + margin]});
        document.querySelectorAll('.ao-climb-row.ao-selected').forEach((r) => r.classList.remove('ao-selected'));
        row.classList.add('ao-selected');
        document.getElementById('route-map').scrollIntoView({behavior: 'smooth', block: 'start'});
    });

    // Lower plot -> map: hover moves the map cursor (positionPoint picks the hovered point with [lat, lon]), zoom fits the map
    function attachLowerPlot(plotDiv, mapId, positionPoint) {
        if (plotDiv._aeroracleHoverSync) {
            return;
        }
        plotDiv._aeroracleHoverSync = true;
        const moveMapCursor = perFrame((position) => {
            const target = plotlyDiv(mapId);
            if (target) {
                setMapCursor(target, ...(position || [null, null]));
            }
        });
        plotDiv.on('plotly_hover', (event) => {
            const point = event.points.find(positionPoint);
            if (point && point.customdata) {
                moveMapCursor([point.customdata[0], point.customdata[1]]);
            }
        });
        plotDiv.on('plotly_unhover', () => moveMapCursor(null));

        // Zoom -> map extent; double click (autorange) -> whole route
        plotDiv.on('plotly_relayout', (event) => {
            const target = plotlyDiv(mapId);
            const range = event['xaxis.range'] || (event['xaxis.range[0]'] !== undefined && [event['xaxis.range[0]'], event['xaxis.range[1]']]);
            if (!target || !(range || event['xaxis.autorange'])) {
                return;  // E.g. the hover line (shapes)
            }
            if (range) {
                fitMapToDistance(target, Math.min(...range), Math.max(...range));
            } else {
                fitMapToDistance(target, null, null);
            }
            renderRiderPower(plotDiv, range ? [Math.min(...range), Math.max(...range)] : null);  // Only the rider power has a grid
            if (!plotDiv._aeroracleClimbZoom) {
                document.querySelectorAll('.ao-climb-row.ao-selected').forEach((r) => r.classList.remove('ao-selected'));
            }
            plotDiv._aeroracleClimbZoom = false;
        });
    }

    function attach(mapId, profileId, attemptsLeft) {
        const mapDiv = plotlyDiv(mapId);
        const profileDiv = plotlyDiv(profileId);
        if (!mapDiv || !profileDiv || !mapDiv.on || !profileDiv.on) {
            if (attemptsLeft > 0) {
                setTimeout(() => attach(mapId, profileId, attemptsLeft - 1), 100);
            }
            return;
        }
        addSummitIcon(mapDiv);
        styleMap(mapDiv);
        if (window.aeroracleApplyPlotTheme) {
            window.aeroracleApplyPlotTheme();  // Light/dark colors of the new figures (theme.js)
        }
        // Listeners survive figure updates of the same graph; the partner graph is looked up per event
        attachLowerPlot(profileDiv, mapId, (p) => p.curveNumber === PROFILE_ELEVATION_TRACE);
        const powerDiv = plotlyDiv(POWER_ID);
        if (powerDiv && powerDiv.on) {
            attachLowerPlot(powerDiv, mapId, (p) => Boolean(p.customdata));
            renderRiderPower(powerDiv, null);  // New figure: whole route
        }

        if (!mapDiv._aeroracleHoverSync) {
            mapDiv._aeroracleHoverSync = true;
            // Map -> profile: customdata of the route trace starts with the distance [km]; null = no line
            const moveProfileLine = perFrame((distance) => {
                [profileId, POWER_ID].forEach((id) => {  // Only the shown one: a hidden plot is not redrawn
                    const target = plotlyDiv(id);
                    if (target && target.offsetParent !== null) {
                        Plotly.relayout(target, distance === null ? {'shapes[0].visible': false}
                            : {'shapes[0].x0': distance, 'shapes[0].x1': distance, 'shapes[0].visible': true});
                    }
                });
            });
            mapDiv.on('plotly_hover', (event) => {
                const point = event.points.find((p) => p.curveNumber === MAP_ROUTE_TRACE);
                if (point && point.customdata) {
                    moveProfileLine(point.customdata[0]);
                }
            });
            mapDiv.on('plotly_unhover', () => moveProfileLine(null));
        }
    }

    window.dash_clientside = Object.assign({}, window.dash_clientside);
    window.dash_clientside.aeroracle = Object.assign({}, window.dash_clientside.aeroracle, {
        // "Color by" menu of the route card: restyle the route trace in the browser (options: color_options())
        recolorMap: function (label, options) {
            const mapDiv = plotlyDiv('route-map');
            const option = options && options[label];
            if (mapDiv && mapDiv.data && option) {
                Plotly.restyle(mapDiv, {
                    'marker.color': [option.color], 'marker.colorscale': [option.colorscale],
                    'marker.cmin': option.cmin, 'marker.cmax': option.cmax, 'marker.colorbar.title.text': option.title,
                    'marker.colorbar.tickmode': option.tickvals ? 'array' : 'auto', 'marker.colorbar.tickvals': [option.tickvals || null],
                    'marker.colorbar.ticktext': [option.ticktext || null],  // Category names, e.g. sun and shade
                }, [MAP_ROUTE_TRACE]);
                // Meaning of the colorbar's ends (plot_interactive.colorbar_end_labels), e.g. tail/head wind
                const ends = option.ends || ['', ''];
                Plotly.relayout(mapDiv, {
                    'annotations[0].text': ends[0], 'annotations[0].visible': Boolean(option.ends),
                    'annotations[1].text': ends[1], 'annotations[1].visible': Boolean(option.ends),
                });
            }
            return window.dash_clientside.no_update;
        },
        // Lower plot of the route card: elevation profile or rider power. A plot drawn while hidden has the wrong size:
        // resized once shown. Returns the hidden flags of the elevation and the power view and the classes of their legends
        // (invisible, but keeping its space: the plot does not move).
        switchProfile: function (view) {
            const power = view === 'power';
            setTimeout(() => {
                const shown = plotlyDiv(power ? POWER_ID : 'route-profile');
                if (shown) {
                    Plotly.Plots.resize(shown);
                }
                const mapDiv = plotlyDiv('route-map');
                const powerDiv = plotlyDiv(POWER_ID);
                if (power && mapDiv && powerDiv && powerDiv.on) {
                    attachLowerPlot(powerDiv, 'route-map', (p) => Boolean(p.customdata));
                    if (!powerDiv.data[0].x || powerDiv.data[0].x.length === 0) {
                        renderRiderPower(powerDiv, null);
                    }
                }
                if (window.aeroracleApplyPlotTheme) {
                    window.aeroracleApplyPlotTheme();
                }
            }, 0);
            const slot = (shown) => 'ao-legend-slot' + (shown ? '' : ' ao-invisible');
            return [power, !power, slot(!power), slot(power)];
        },
        // Called whenever new figures are rendered; attaches the listeners once per graph
        hoverSync: function (mapFigure, profileFigure) {
            if (mapFigure && profileFigure) {
                attach('route-map', 'route-profile', 50);
            }
            return window.dash_clientside.no_update;
        },
    });
})();
