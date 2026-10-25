import argparse
import os
import sys
from datetime import datetime
from enum import StrEnum
from typing import NamedTuple

import dateutil.parser
import numpy as np

import config
import utilities


class PerGradient(NamedTuple):
    """A setting that depends on the gradient: flat, uphill (>= threshold), downhill (<= -threshold)."""
    flat: float
    uphill: float
    downhill: float


class PowerSummary(NamedTuple):
    average_w: float     # Average power [W]
    normalized_w: float  # Normalized power (NP) [W]


class WeatherProvider(StrEnum):
    OPEN_METEO = 'o'
    METEOBLUE = 'm'
    VISUAL_CROSSING = 'v'


class Parameters:
    def __init__(self, argv: list[str] | None = None):
        """Parse the command line, or argv if given (e.g. by the web app)."""
        self.args = self.parse_parameters(argv)
        self.folder_prefix = config.DATA_DIR  # Folder containing GeoData/
        self.temp_folder = config.TEMP_DIR
        if not self.check_data:
            self.check_parameters()

    def parse_parameters(self, argv=None):
        parse = argparse.ArgumentParser(
            description="Program for cycling aerodynamics calculations and ride time estimations")
        parse.add_argument('-i', '--input', type=str, help='Input file', default=None)
        parse.add_argument('-p', '--power', nargs='+', type=int, help='Average Power [W] [Flat, Uphill, Downhill]; Default: [210, 231, 0]', default=None)
        parse.add_argument('-w', '--weight', type=float, help='System weight (rider + bike) [kg]; Default: 80',
                           default=None)
        parse.add_argument('-t', '--time', type=str, help='Start time of the ride, local time at the route start [yyyymmddHHMMSS]', default=None)
        parse.add_argument('-q', '--quiet', action='store_true', help='Suppress output')
        parse.add_argument('-o', '--output', type=str, help='Output file name', default=None)
        parse.add_argument('-u', '--url', type=str, help='URL', default=None)
        parse.add_argument('-b', '--tt', action='store_true', help='Time trial bike')
        parse.add_argument('-r', '--crr', type=float, help='Rolling resistance coefficient', default=None)
        parse.add_argument('-c', '--cda', nargs='+', type=float,
                           help='Coefficient of drag (CdA) [Flat, Uphill, Downhill]', default=None)
        parse.add_argument('-m', '--powerfactor', type=float, help='Factor for uphill power (gradient >= --gradient)',
                           default=None)
        parse.add_argument('-g', '--gradient', type=float, help='Gradient threshold for uphill/downhill power and CdA [-] (uphill: >= +value, downhill: <= -value); Default: 0.05 (= 5%%)',
                           default=0.05)
        parse.add_argument('-a', '--weather', type=str, help='Weather provider to be used [o: OpenMeteo (Default), m: MeteoBlue, v: VisualCrossing]',
                           choices=['m', 'o', 'v'], default='o')
        parse.add_argument('-k', '--climbs', action='store_true', help='Automatic Climb detection and annotation as a cue')
        parse.add_argument('-s', '--whatif', type=float, help='Change of each parameter in the what-if table [%%]; Default: whatif_pct in config.toml (1)',
                           default=config.WHATIF_PCT)
        parse.add_argument('--check-data', action='store_true', help='Report the available data sets, credentials and programs, then exit')

        args = parse.parse_args(argv)
        self.__setattr__('input', args.input)
        self.__setattr__('power', args.power)
        self.__setattr__('weight', args.weight)
        self.__setattr__('time', args.time)
        self.__setattr__('quiet', args.quiet)
        self.__setattr__('output', args.output)
        self.__setattr__('url', args.url)
        self.__setattr__('tt', args.tt)
        self.__setattr__('crr', args.crr)
        self.__setattr__('cda', args.cda)
        self.__setattr__('powerfactor', args.powerfactor)
        self.__setattr__('gradient_threshold', abs(args.gradient))
        self.__setattr__('weather', args.weather)
        self.__setattr__('dem', [])
        self.__setattr__('climbs', args.climbs)
        self.__setattr__('check_data', args.check_data)
        self.__setattr__('whatif_pct', abs(args.whatif))

    def check_parameters(self):
        if self.input is None:
            utilities.display_error_message('No input filename provided (mandatory). Exiting.')
            sys.exit()
        elif not os.path.dirname(self.input) and os.path.exists(os.path.join(config.INPUT_DIR, self.input)):
            self.input = os.path.join(config.INPUT_DIR, self.input)  # Bare file name found in the input folder
        if not os.path.exists(self.input) and self.url is None:
            utilities.display_error_message(f"File {self.input} could not be found and no URL provided. Exiting.")
            sys.exit()
        if self.powerfactor is None:
            self.powerfactor = 1.1
        if self.power is None:
            self.power = [210, 210*self.powerfactor, 1]
            print(f'- No power provided. Defaulting to {self.power[0]}/{self.power[1]}/{self.power[2]} W.')
        elif len(self.power) == 1:
            self.power = [self.power[0], self.power[0]*self.powerfactor, .1]
        elif len(self.power) == 3:
            self.power[2] = np.max([.1, self.power[2]]) # Making sure that downhill power is not set to zero
        elif len(self.power) == 2:
            self.power.append(.1)  # Making sure that downhill power is not set to zero
        if self.weight is None:
            self.weight = 80
            print('- No weight provided. Defaulting to 80 kg.')
        if self.time is None:
            self.time = False
        else:
            if not utilities.check_internet_connection():
                self.time = False
                utilities.display_error_message('No internet connection - continuing without weather data')
            else:
                self.time = dateutil.parser.parse(self.time)  # Unix timestamp once the route is known: utilities.set_start_time
        if self.output is None:
            self.output = True
        if self.quiet is None:
            self.quiet = False
        if self.url is None:
            self.rwgps_json = None
        else:
            self.rwgps_json = os.path.splitext(self.input)[0] + '.json'
        if not self.tt:
            if self.cda is None or len(self.cda) !=3:
                if self.cda is not None and len(self.cda) == 1:
                    self.cda = self.cda*3
                else:
                    self.cda = [0.38, 0.40, 0.29]
            elif len(self.cda) == 3:
                pass
        elif self.tt:
            if self.cda is None or len(self.cda) !=3:
                if self.cda is not None and len(self.cda) == 1:
                    self.cda = self.cda*3
                else:
                    self.cda = [0.23, 0.23, 0.23]
            elif len(self.cda) == 3:
                pass
        if self.crr is None:
            self.crr = 0.00321
        self.power = PerGradient(*self.power)
        self.cda = PerGradient(*self.cda)
        self.weather = WeatherProvider(self.weather)

    def check_URL(self):
        import re
        pattern = r'\d+\.tcx\?'  # regular expression matching one or more digits, followed by “.tcx”, followed by a question mark
        if self.url.find('ridewithgps.com') != -1:
            if not re.search(pattern, self.url):
                self.url = self.url.replace('?', '.tcx?')
