// Light/dark mode following the system setting (prefers-color-scheme), also when it changes while the page is open:
// - page: Bootstrap 5.3 color modes (data-bs-theme on <html>)
// - figures: colors of the route map and the elevation profile (see plot_interactive.py)

(function () {
    const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');

    // Same values as THEMES in plot_interactive.py (figure backgrounds are transparent)
    const PLOT_THEMES = {
        light: {font: '#4a5866', line: '#15202b', grid: '#e3e8ed', labelBg: 'rgba(255,255,255,0.92)', labelBorder: '#d6dde4'},
        dark: {font: '#a9b6c3', line: '#e6edf3', grid: '#2a3643', labelBg: 'rgba(23,32,42,0.92)', labelBorder: '#2a3643'},
    };
    const PROFILE_ELEVATION_TRACE = 1;

    function plotlyDiv(id) {
        const container = document.getElementById(id);
        return container && container.querySelector('.js-plotly-plot');
    }

    function currentTheme() {
        return darkQuery.matches ? 'dark' : 'light';
    }

    // Applies the plot theme to the figures that are currently shown, if they differ (called by hover_sync.js after
    // rendering). The server builds the figures in the theme of the page load, so this only acts after a system change.
    window.aeroracleApplyPlotTheme = function () {
        const theme = PLOT_THEMES[currentTheme()];
        const mapDiv = plotlyDiv('route-map');
        const profileDiv = plotlyDiv('route-profile');
        if (mapDiv && mapDiv.layout && mapDiv.layout.font && mapDiv.layout.font.color !== theme.font) {
            Plotly.relayout(mapDiv, {'font.color': theme.font});
        }
        if (profileDiv && profileDiv.layout && profileDiv.layout.font && profileDiv.layout.font.color !== theme.font) {
            const update = {'font.color': theme.font, 'yaxis.gridcolor': theme.grid, 'hoverlabel.bgcolor': theme.labelBg,
                            'hoverlabel.bordercolor': theme.labelBorder, 'hoverlabel.font.color': theme.line};
            (profileDiv.layout.annotations || []).forEach((_, i) => {
                update[`annotations[${i}].font.color`] = theme.line;
                update[`annotations[${i}].bgcolor`] = theme.labelBg;
            });
            Plotly.relayout(profileDiv, update);
            Plotly.restyle(profileDiv, {'line.color': theme.line}, [PROFILE_ELEVATION_TRACE]);
        }
    };

    function applyTheme() {
        document.documentElement.setAttribute('data-bs-theme', currentTheme());
        window.aeroracleApplyPlotTheme();
    }

    applyTheme();
    darkQuery.addEventListener('change', applyTheme);

    window.dash_clientside = Object.assign({}, window.dash_clientside);
    window.dash_clientside.aeroracle = Object.assign({}, window.dash_clientside.aeroracle, {
        systemTheme: function () {
            return currentTheme();
        },
    });
})();
