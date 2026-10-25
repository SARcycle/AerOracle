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
            // Profile -> map: customdata of the elevation trace starts with [lat, lon]
            profileDiv.on('plotly_hover', (event) => {
                const point = event.points.find((p) => p.curveNumber === PROFILE_ELEVATION_TRACE);
                const target = plotlyDiv(mapId);
                if (point && point.customdata && target) {
                    Plotly.restyle(target, {lat: [[point.customdata[0]]], lon: [[point.customdata[1]]]}, [MAP_CURSOR_TRACE]);
                }
            });
            profileDiv.on('plotly_unhover', () => {
                const target = plotlyDiv(mapId);
                if (target) {
                    Plotly.restyle(target, {lat: [[null]], lon: [[null]]}, [MAP_CURSOR_TRACE]);
                }
            });
        }

        if (!mapDiv._aeroracleHoverSync) {
            mapDiv._aeroracleHoverSync = true;
            // Map -> profile: customdata of the route trace starts with the distance [km]
            mapDiv.on('plotly_hover', (event) => {
                const point = event.points.find((p) => p.curveNumber === MAP_ROUTE_TRACE);
                const target = plotlyDiv(profileId);
                if (point && point.customdata && target) {
                    const distance = point.customdata[0];
                    Plotly.relayout(target, {'shapes[0].x0': distance, 'shapes[0].x1': distance, 'shapes[0].visible': true});
                }
            });
            mapDiv.on('plotly_unhover', () => {
                const target = plotlyDiv(profileId);
                if (target) {
                    Plotly.relayout(target, {'shapes[0].visible': false});
                }
            });
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
