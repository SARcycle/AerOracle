// Synchronized hover between the route map and the elevation profile (see plot_interactive.py):
// - hovering the profile moves a marker to the corresponding location on the map
// - hovering the route on the map moves a vertical line to the corresponding distance in the profile
// Runs entirely in the browser (Plotly.restyle / relayout), without server round trips.

(function () {
    const MAP_ROUTE_TRACE = 0;
    const MAP_CURSOR_TRACE = 1;
    const PROFILE_ELEVATION_TRACE = 1;

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
        if (window.aeroracleApplyPlotTheme) {
            window.aeroracleApplyPlotTheme();  // Light/dark colors of the new figures (theme.js)
        }
        // Listeners survive figure updates of the same graph; the partner graph is looked up per event
        if (!profileDiv._aeroracleHoverSync) {
            profileDiv._aeroracleHoverSync = true;
            // Profile -> map: customdata of the elevation trace starts with [lat, lon]; null = no cursor
            const moveMapCursor = perFrame((position) => {
                const target = plotlyDiv(mapId);
                if (target) {
                    setMapCursor(target, ...(position || [null, null]));
                }
            });
            profileDiv.on('plotly_hover', (event) => {
                const point = event.points.find((p) => p.curveNumber === PROFILE_ELEVATION_TRACE);
                if (point && point.customdata) {
                    moveMapCursor([point.customdata[0], point.customdata[1]]);
                }
            });
            profileDiv.on('plotly_unhover', () => moveMapCursor(null));
        }

        if (!mapDiv._aeroracleHoverSync) {
            mapDiv._aeroracleHoverSync = true;
            // Map -> profile: customdata of the route trace starts with the distance [km]; null = no line
            const moveProfileLine = perFrame((distance) => {
                const target = plotlyDiv(profileId);
                if (target) {
                    Plotly.relayout(target, distance === null ? {'shapes[0].visible': false}
                        : {'shapes[0].x0': distance, 'shapes[0].x1': distance, 'shapes[0].visible': true});
                }
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
        // Called whenever new figures are rendered; attaches the listeners once per graph
        hoverSync: function (mapFigure, profileFigure) {
            if (mapFigure && profileFigure) {
                attach('route-map', 'route-profile', 50);
            }
            return window.dash_clientside.no_update;
        },
    });
})();
