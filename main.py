import numpy as np
import os
import shutil
import sys

import assets
import config
import datacheck
import parameters
import speedcalculation
import utilities
import tcx_writer

try:  # Usage statistics (usage.db)
    import tracker
    TRACKING_ENABLED = True
except ImportError:
    TRACKING_ENABLED = False


def terminal_link(label, url):
    print(f'{label}: \033]8;;{url}\033\\{url}\033]8;;\033\\')


def publish(args):
    """Move the output files to config.PUBLISH_DIR and print their links below config.PUBLISH_URL."""
    route = os.path.splitext(args.input)[0]
    outputs = {'TCX': args.input, **{suffix[1:].upper(): route + suffix for suffix in tcx_writer.GPSBABEL_FORMATS},
               'Wind chart': utilities.meteo_chart_path(args.input)}
    for label, output_file in outputs.items():
        if os.path.exists(output_file):
            shutil.move(output_file, os.path.join(config.PUBLISH_DIR, os.path.basename(output_file)))
            if not args.quiet:
                terminal_link(label, f'{config.PUBLISH_URL}/{os.path.basename(output_file)}')
    if not args.quiet and os.path.exists(os.path.join(config.PUBLISH_DIR, os.path.basename(route + '.gpx'))):
        terminal_link('Shade Map', f'https://shademap.app/gpxreplay/?file={config.PUBLISH_URL}/{os.path.basename(route)}.gpx')


def main():
    args = parameters.Parameters()
    if args.check_data:
        sys.exit(0 if datacheck.report() else 1)
    data = assets.Assets(args)
    utilities.set_start_time(args, data)

    distance_km = None  # Logged in usage.db
    elevation_m = None
    analysis_success = False

    try:
        data = speedcalculation.__init__(args, data)
        if args.climbs:
            utilities.climb_detection(data)
        data = utilities.correct_peak_location(args, data)
        if args.time is not False:
            utilities.plot_wind_conditions(args, data)

        if args.output:
            tcx_writer.write_tcx(args.input, data.track)

            for suffix in tcx_writer.GPSBABEL_FORMATS:
                if not tcx_writer.export(args.input, args.input.replace('.tcx', suffix), data.track):
                    print(f'- {suffix[1:].upper()} conversion failed (gpsbabel missing or too old)')

        timestamp = data.track['timestamp']
        ride_time_s = timestamp[-1] - timestamp[0]
        height_diff = np.diff(data.track['elevation_m'])
        distance_km = data.track['distance_m'][-1] / 1000
        elevation_m = int(np.sum(height_diff[height_diff > 0]))
        analysis_success = True

        if not args.quiet:
            hours = ride_time_s / 3600
            minutes = 60 * (hours % 1)
            seconds = 60 * (minutes % 1)

            print('==============================')
            print('       Ride Information       ')
            print('==============================')
            print(f'Distance: {np.floor(100 * distance_km) / 100:.2f} km')
            print(f'Riding Time: {int(np.floor(hours)):02d}:{int(np.floor(minutes)):02d}:{int(np.floor(seconds)):02d}')
            print(f'Average Speed: {np.round(100 * (distance_km * 1000 / ride_time_s * 3.6)) / 100:.2f} km/h')
            print(f'Height Difference: +{elevation_m:d} m/{int(np.sum(height_diff[height_diff < 0])):d} m')
            print(f'Resulting Power: Avg {args.power_out.average_w:.0f} W | NP {args.power_out.normalized_w:.0f} W')
            print(f'Estimated kcal Burned: {(args.power_out.average_w*ride_time_s)/1000:.0f} kcal')
            print('Riding time if only one parameter changes (- = faster, + = slower):')
            print(f'  {"":28s} {f"-{args.whatif_pct:g}%":>10s} {f"+{args.whatif_pct:g}%":>10s}')
            for sensitivity in speedcalculation.sensitivities(args, data.track):
                print(f'  {f"{sensitivity.parameter} (±{sensitivity.step})":28s} '
                      f'{utilities.format_time_change(sensitivity.minus_s):>10s} {utilities.format_time_change(sensitivity.plus_s):>10s}')
            if hasattr(data, 'tunnel_distance'):
                print(f'{data.tunnel_distance:0.2f} km of tunnels/underpasses along the route')
            if args.time:
                 utilities.sunCalc(args, data)
        if config.PUBLISHING and args.output:
            publish(args)

    except Exception as e:
        analysis_success = False
        print(f"Error during analysis: {e}")
        raise

    finally:
        if TRACKING_ENABLED:
            try:
                tracker.log_analysis(
                    distance_km=distance_km,
                    elevation_gain_m=elevation_m,
                    success=analysis_success
                )
            except Exception as tracking_error:  # Statistics must not stop the analysis
                print(f"Warning: Could not log analysis: {tracking_error}")


if __name__ == "__main__":
    main()
