# Beacon Grid Test - Klipper Module
#
# Utility to analyse and apply gantry twist compensation using beacon offset data.
#
# Copyright (C) 2025 omgitsgio <gio@omgitsgio.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from datetime import datetime
import json
import logging
import os
import threading
from pathlib import Path
import copy
import gc
from .graph_generator import GraphGenerator
from .axis_twist_comp_utility import AxisTwistCompUtility


class GantryTwistUtility:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.name = config.get_name()
        
        # Configuration - defaults for the grid
        self.graphs_folder = config.get('graphs_folder', '~/printer_data/config/Gantry_twist_analysis')
        self.min_x = config.getfloat('min_x', 22.0, minval=0, maxval=280.0) # min y = 20 max y = 300
        self.max_x = config.getfloat('max_x', 283.0, minval=20, maxval=300.0)
        self.min_y = config.getfloat('min_y', 22.0, minval=20, maxval=300.0)
        self.max_y = config.getfloat('max_y', 283.0, minval=20, maxval=300.0)
        self.comp_y_position = config.getfloat('calibrate_y', None, minval=20.0, maxval=300.0)
        self.sampling_direction = config.get('sampling_direction', 'xy')  # 'x', 'y', 'xy', 'yx'
        self.mode = config.getint('mode', 0, minval=0)  # probe to use: 0='analysis' or 1='compensation'
        self.grid_size = config.getint('grid_size', 10, minval=2)
        self.default_z_height = config.getfloat('z_height', 2.0, minval=1, maxval=5.0)
        self.default_bed_temp = config.getfloat('bed_temp', 0.0)
        self.default_hotend_temp = config.getfloat('hotend_temp', 0.0)
        self.settle_delay = config.getfloat('settle_delay', 1.0)
        self.point_delay = config.getfloat('point_delay', 1.0)
        self.travel_speed = config.getfloat('travel_speed', 5000.0, maxval=20000.0)
        self.max_retries = config.getint('max_retries', 3, minval=0, maxval=50)
        self.save_raw_data = config.getboolean('save_raw_data', False)

        # Mode name container (assigned by cmd_GANTRY_TWIST_UTILITY)
        self.mode_names = []

        # Metadata to pass to GraphGenerator
        self.meta = {
            'name': self.name,
            'mode': None,
            'sampling_direction': self.sampling_direction,
            'default_z_height': self.default_z_height,
            'settle_delay': self.settle_delay,
            'point_delay': self.point_delay,
            'travel_speed': self.travel_speed,
            'bounds': {
                'min_x': float(self.min_x),
                'max_x': float(self.max_x),
                'min_y': float(self.min_y),
                'max_y': float(self.max_y),
            },
        }

        # Save offset data separately within graphs folder
        self.raw_data_folder = config.get('raw_data_folder', f'{self.graphs_folder}/Beacon_offset_raw_data')
        self.raw_data_folder = Path(self.raw_data_folder).expanduser()
        self.raw_data_folder.mkdir(parents=True, exist_ok=True)

        self.beacon_safe_home_pos = None

        # Initialize graph processor
        self.graph = GraphGenerator

        # Calibration GCode to run before testing
        self.pre_test_gcode = config.get('pre_test_gcode', 
            '_SETTLE_PRINT_BED\nBEACON_AUTO_CALIBRATE\nZ_TILT_ADJUST\nG28 Z')
        
        # State
        self.test_running = False
        self.collected_data = []  # Store all grid test results
        self.graph_thread = None  # Background thread for graph generation
        
        # Register event handlers
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        
        # Register GCode commands
        self.gcode.register_command(
            'GANTRY_TWIST_UTILITY',
            self.cmd_GANTRY_TWIST_UTILITY,
            desc=self.cmd_GANTRY_TWIST_UTILITY_help
        )
    
    def _handle_ready(self):
        """Called when Klipper is ready."""
        # Get references to required objects
        try:
            self.beacon = self.printer.lookup_object('beacon')
            self.toolhead = self.printer.lookup_object('toolhead')
            logging.info("GantryTwistUtility: Initialized successfully")
        except Exception as e:
            raise self.printer.config_error(
                f"GantryTwistUtility requires 'beacon' probe: {e}"
            )
        
        # Get safe xy home position from beacon config
        self.beacon_safe_home_pos = getattr(getattr(self.beacon, 'homing_helper', None), 'home_pos', None)
        if self.beacon_safe_home_pos is None:
            logging.warning("GantryTwistUtility: Unable to determine beacon safe home position.")  

        # Mirror to meta for consistency so exporters don't recompute
        try:
            if self.beacon_safe_home_pos and len(self.beacon_safe_home_pos) >= 2:
                self.meta['beacon_safe_home_pos'] = [
                    self.beacon_safe_home_pos[0],
                    self.beacon_safe_home_pos[1]
                ]
            else:
                self.meta['beacon_safe_home_pos'] = None
        except Exception:
            self.meta['beacon_safe_home_pos'] = None
    
    def _heat_and_wait(self, gcmd, bed_temp, hotend_temp):
        """Heat bed and hotend to specified temperatures."""
        if bed_temp <= 0 and hotend_temp <= 0:
            gcmd.respond_info("Skipping heating - temps set to zero")
            return
        
        gcmd.respond_info(f"Heating bed to {bed_temp}°C and hotend to {hotend_temp}°C")
        
        # Start heating both
        if bed_temp > 0:
            self.gcode.run_script_from_command(f"M140 S{bed_temp}")
        if hotend_temp > 0:
            self.gcode.run_script_from_command(f"M104 S{hotend_temp}")
        
        # Wait for both
        if bed_temp > 0:
            self.gcode.run_script_from_command(f"M190 S{bed_temp}")
        if hotend_temp > 0:
            self.gcode.run_script_from_command(f"M109 S{hotend_temp}")

        # Wait 1 minute after heating to stabilize temps
        self.gcode.run_script_from_command(f"G4 P60000")
    
    def _run_pre_test_calibration(self, gcmd):
        """Run pre-test calibration sequence."""
        if not self.pre_test_gcode:
            return
        
        gcmd.respond_info("Running pre-test calibration...")
        
        # Parse and run each line
        for line in self.pre_test_gcode.split('\n'):
            line = line.strip()
            if line and not line.startswith('#'):
                try:
                    self.gcode.run_script_from_command(line)
                    self.toolhead.wait_moves()
                except Exception as e:
                    # If a fatal Klipper error is detected, abort immediately
                    if self._is_fatal_klipper_error(e):
                        raise gcmd.error(f"Fatal Klipper error during pre-test command '{line}': {e}")
                    gcmd.respond_info(f"Warning: Pre-test command '{line}' failed: {e}")

    def _generate_grid_points(self, gcmd, min_x, max_x, min_y, max_y, grid_size):
        """Generate grid points for testing based on sampling direction or mode."""
        points = []
        
        # Helper function to calculate position along an axis
        def calc_pos(min_val, max_val, idx, size):
            if size == 1:
                return (min_val + max_val) / 2.0
            else:
                return min_val + (max_val - min_val) * idx / (size - 1)

        # Mode 1 (compensation) samples X-axis only at center Y
        if self.mode == 1:
            center_y = self.beacon_safe_home_pos[1] if self.comp_y_position else self.beacon_safe_home_pos[1]
            for x_idx in range(grid_size):
                x_pos = calc_pos(min_x, max_x, x_idx, grid_size)
                points.append((x_pos, center_y))
        
        # Mode 0 (analysis) - use sampling_direction
        elif self.mode == 0:
            if self.sampling_direction == 'x':
                # Sample all X positions at each Y, then move to next Y
                for y_idx in range(grid_size):
                    y_pos = calc_pos(min_y, max_y, y_idx, grid_size)
                    for x_idx in range(grid_size):
                        x_pos = calc_pos(min_x, max_x, x_idx, grid_size)
                        points.append((x_pos, y_pos))
            
            elif self.sampling_direction == 'y':
                # Sample all Y positions at each X, then move to next X
                for x_idx in range(grid_size):
                    x_pos = calc_pos(min_x, max_x, x_idx, grid_size)
                    for y_idx in range(grid_size):
                        y_pos = calc_pos(min_y, max_y, y_idx, grid_size)
                        points.append((x_pos, y_pos))
            
            elif self.sampling_direction == 'xy':
                # Serpentine pattern: scan X, then move up Y and scan X backwards
                for y_idx in range(grid_size):
                    y_pos = calc_pos(min_y, max_y, y_idx, grid_size)
                    
                    # Alternate X direction based on Y row (even rows: left-to-right, odd: right-to-left)
                    if y_idx % 2 == 0:
                        x_range = range(grid_size)
                    else:
                        x_range = range(grid_size - 1, -1, -1)
                    
                    for x_idx in x_range:
                        x_pos = calc_pos(min_x, max_x, x_idx, grid_size)
                        points.append((x_pos, y_pos))
            
            elif self.sampling_direction == 'yx':
                # Serpentine pattern: scan Y, then move across X and scan Y backwards
                for x_idx in range(grid_size):
                    x_pos = calc_pos(min_x, max_x, x_idx, grid_size)
                    
                    # Alternate Y direction based on X column (even cols: bottom-to-top, odd: top-to-bottom)
                    if x_idx % 2 == 0:
                        y_range = range(grid_size)
                    else:
                        y_range = range(grid_size - 1, -1, -1)
                    
                    for y_idx in y_range:
                        y_pos = calc_pos(min_y, max_y, y_idx, grid_size)
                        points.append((x_pos, y_pos))
            
            else:
                raise ValueError(f"Invalid sampling_direction: {self.sampling_direction}")
        
        else:
            raise ValueError(f"Invalid mode: {self.mode}")
        
        total_points = len(points)
        if self.mode == 0:  # 'analysis'
            gcmd.respond_info(f"Starting grid test: {grid_size}x{grid_size} = {total_points} points")
            gcmd.respond_info(f"Area: X{min_x:.1f}-{max_x:.1f}, Y{min_y:.1f}-{max_y:.1f}")
        elif self.mode == 1:  # 'compensation'
            gcmd.respond_info(f"Starting axis compensation utility with sample size: {total_points} points")
            gcmd.respond_info(f"X range: {min_x:.1f}-{max_x:.1f}, Y: {points[0][1]:.1f}")

        return points

    def _is_fatal_klipper_error(self, err) -> bool:
        """This is to separate fatal Klipper errors from recoverable ones 
        to avoid loops returning redundant warnings.
        """

        msg = (str(err) or "").lower()
        fatal_markers = [
            'mcu shutdown',
            "mcu 'mcu' shutdown",
            'timer too close',
            'firmware restart',
            'firmware_restart',
            'shutdown due to',
            'lost communication with mcu',
            'communication timeout',
            'printer is not ready',
            'heater decoupled',
            'heater not heating',
            'heater not heating at expected rate',
            'thermistor out of range',
            'adc out of range',
        ]
        return any(marker in msg for marker in fatal_markers)
    
    def _run_grid_test(self, gcmd, min_x, max_x, min_y, max_y, grid_size, z_height, max_retries):
        """Execute the grid test."""
        # Generate grid points
        points = self._generate_grid_points(gcmd, min_x, max_x, min_y, max_y, grid_size)
        
        total_points = len(points)
        
        gcmd.respond_info(f"Z height: {z_height}mm")

        # Execute grid test
        self.test_running = True
        completed = 0
        failed = 0
        
        try:
            for idx, (x_pos, y_pos) in enumerate(points, 1):
                if not self.test_running:
                    gcmd.respond_info("Grid test cancelled. Exiting...")
                    break
                
                gcmd.respond_info(f"Point {idx}/{total_points}: X{x_pos:.2f} Y{y_pos:.2f}")
                
                try:
                    # Move to position
                    self.gcode.run_script_from_command(
                        f"G1 X{x_pos:.3f} Y{y_pos:.3f} Z{z_height:.3f} F{self.travel_speed:.0f}"
                    )
                    self.toolhead.wait_moves()
                    
                    # Wait for stabilization
                    self.toolhead.dwell(self.settle_delay)
                    
                    # Run offset compare
                    compare_params = {
                        "SAMPLES_TOLERANCE_RETRIES": gcmd.get_int('SAMPLES_TOLERANCE_RETRIES', max_retries)
                    }
                    compare_gcmd = self.gcode.create_gcode_command(
                        "BEACON_OFFSET_COMPARE",
                        "BEACON_OFFSET_COMPARE",
                        compare_params
                    )
                    self.beacon.cmd_BEACON_OFFSET_COMPARE(compare_gcmd)
                    self.toolhead.wait_moves()
                    
                    # === DATA COLLECTION SYSTEM ===
                    # Collect data from beacon's last_offset_result
                    # Structure: {'position': (x, y, contact_z), 'delta': contact_z - proximity_z}
                    if hasattr(self.beacon, 'last_offset_result') and self.beacon.last_offset_result:
                        result = self.beacon.last_offset_result
                        # result format: { 'position': (x, y, contact_z), 'delta': delta }
                        contact_z = float(result['position'][2])
                        delta = float(result['delta'])
                        proximity_z = contact_z - delta

                        data_point = {
                            'grid_index': idx,
                            'x': float(x_pos),
                            'y': float(y_pos),
                            'z_commanded': float(z_height),
                            'contact_z': contact_z,
                            'proximity_z': proximity_z,
                            'delta': delta,
                            'delta_um': delta * 1000.0,
                        }
                        self.collected_data.append(data_point)

                        # Debug
                        # gcmd.respond_info(
                        #     "  Beacon Offset Grid Test:\n"
                        #     f"    Contact: {contact_z:.5f}mm\n"
                        #     f"    Proximity: {proximity_z:.5f}mm\n"
                        #     f"    Delta: {delta*1000:.3f}µm"
                        # )

                    else:
                        gcmd.respond_info("Warning: No offset result data available. Exiting...")
                        break
                    
                    # Wait between points
                    self.toolhead.dwell(self.point_delay)
                    
                    completed += 1
                    
                except Exception as e:
                    # Abort entirely on known fatal Klipper errors
                    if self._is_fatal_klipper_error(e):
                        self.test_running = False
                        raise gcmd.error(f"Fatal Klipper error at point {idx}: {e}")
                    gcmd.respond_info(f"Error at point {idx}: {e}")
                    failed += 1
        
        finally:
            self.test_running = False

        # Report results
        gcmd.respond_info("=" * 60)
        
        if self.mode == 0:  # 'analysis'
            gcmd.respond_info(f"Grid test complete: {completed}/{total_points} points successful")
        elif self.mode == 1:  # 'compensation'
            gcmd.respond_info(f"Axis compensation points collection complete: {completed}/{total_points} points successful")
        if failed > 0:
            gcmd.respond_info(f"Failed points: {failed}")
        
        gcmd.respond_info("=" * 60)
        
        # Add completed/failed counts to metadata
        self.meta.update({
            'points_completed': completed,
            'total_points': total_points,
            'points_failed': failed,
        })
    
    def clear_collected_data(self):
        """Clear the collected data."""
        self.collected_data = []

    # DEBUG #
    def _load_debug_raw_data(self, gcmd):
        """Load raw data from JSON for DEBUG mode and populate collected_data.

        Expects a file named by self.debug_raw_data_file inside self.debug_raw_data_dir.
        Minimal processing: directly assigns parsed JSON (list of dicts),
        only backfilling proximity_z when missing.
        """
        debug_raw_data_dir = Path('~/printer_data/config/Beacon_raw_data').expanduser()
        debug_raw_data_file = 'raw_data.json'

        json_path = (debug_raw_data_dir / debug_raw_data_file)
        if not json_path.exists():
            raise gcmd.error(
                f"DEBUG mode: raw data file not found: {json_path}. "
                f"Place your JSON at this path or configure debug_raw_data_dir/debug_raw_data_file."
            )
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
        except Exception as e:
            raise gcmd.error(f"DEBUG mode: failed to read JSON file: {e}")

        # Check if JSON has the expected structure: {"meta": {...}, "data": [...]}
        if isinstance(data, dict) and 'meta' in data and isinstance(data['meta'], dict):
            self.meta.update(data['meta'])
            data = data.get('data', [])
        
        # Validate data is a list of dicts
        if not isinstance(data, list) or not all(isinstance(pt, dict) for pt in data):
            raise gcmd.error("DEBUG mode: JSON 'data' field must be a list of objects (dicts)")
            
        if len(data) < 2:
            raise gcmd.error("DEBUG mode: Not enough data points (need at least 2)")

        # Replace collected data with the debug data directly
        self.collected_data = data
        # END DEBUG #

    def _generate_graphs_background(self, gcmd, graphs_folder, data_snapshot, debug: bool = False):
        """Background thread worker for graph generation to avoid blocking Klipper."""
        try:
            logging.info("Graph generation started in background...")

            dashboard_filename = GraphGenerator(gcmd, graphs_folder, data_snapshot, debug=debug, meta=self.meta).plot_analysis()
            if dashboard_filename:
                gcmd.respond_info(f"Graphs saved in: {os.path.basename(os.path.dirname(dashboard_filename))}")
            else:
                gcmd.respond_info("Warning: Graph generation completed but no file path returned")
        except Exception as e:
            gcmd.respond_info(f"Error during background graph generation: {e}")
            logging.exception("Graph generation failed in background thread")
        finally:
            # Best-effort cleanup to free memory after plotting
            try:
                del data_snapshot
                self.graph_thread = None
                gc.collect()
            except Exception:
                pass
    
    # GCode Commands

    cmd_GANTRY_TWIST_UTILITY_help = (
        "Run the gantry twist utility using beacon offset.\n"
        "Parameters: DEBUG=0|1 (when 1, skip moves and load JSON from ~/printer_data/config/Beacon_raw_data/raw_data.json).\n"
        "Optional: BED_TEMP, HOTEND_TEMP, Z_HEIGHT, MIN_X, MAX_X, MIN_Y, MAX_Y, GRID_SIZE, MAX_RETRIES."
    )
    def cmd_GANTRY_TWIST_UTILITY(self, gcmd):
        """Entry point for GANTRY_TWIST_UTILITY GCode command."""
        # Get parameters
        bed_temp = gcmd.get_float('BED_TEMP', self.default_bed_temp)
        hotend_temp = gcmd.get_float('HOTEND_TEMP', self.default_hotend_temp)
        grid_size = gcmd.get_int('GRID_SIZE', self.grid_size, minval=2, maxval=50)
        max_retries = gcmd.get_int('MAX_RETRIES', self.max_retries, minval=0, maxval=100)
        debug = gcmd.get_int('DEBUG', 0) # DEBUG
        self.mode = gcmd.get_int('MODE', self.mode, minval=0)  # 'analysis' or 'compensation'
        self.comp_y_position = gcmd.get_float('CALIBRATE_Y', self.comp_y_position, minval=20.0, maxval=300.0)

        # Set the mode string
        self.mode_names = ['analysis', 'compensation']
        if 0 <= self.mode < len(self.mode_names):
            self.mode_name = self.mode_names[self.mode].capitalize()
        else:
            gcmd.respond_info(f"Invalid mode value: {self.mode}. Exiting...")
            return

        # Add/amend parameters to meta
        self.meta.update({
            'bed_temp': bed_temp,
            'hotend_temp': hotend_temp,
            'grid_size': grid_size,
            'max_retries': max_retries,
            'mode': self.mode_name,
        })

        # Safe home position from beacon config
        if self.beacon_safe_home_pos is None:
            gcmd.respond_info("Warning: Beacon safe home position unknown. Using grid center as fallback.")
            self.beacon_safe_home_pos = (self.max_x + self.min_x) / 2.0, (self.max_y + self.min_y) / 2.0

        home_x = self.beacon_safe_home_pos[0] if self.beacon_safe_home_pos else None
        home_y = self.beacon_safe_home_pos[1] if self.beacon_safe_home_pos else None

        if self.test_running:
            raise gcmd.error("Grid test already running")
        
        # Clear previous data
        self.clear_collected_data()
        
        gcmd.respond_info("=" * 60)
        gcmd.respond_info("  GANTRY TWIST UTILITY  ")
        gcmd.respond_info(f"  Mode: {self.mode_name}  ")
        gcmd.respond_info("=" * 60)
        
        # DEBUG #
        try:
            if debug:
                # DEBUG mode: skip all mechanical actions and load data from JSON
                gcmd.respond_info("DEBUG=1: Skipping heating, homing, calibration, and probing. Loading raw JSON...")
                self.clear_collected_data()
                self._load_debug_raw_data(gcmd)

                # Schedule background graph generation
                gcmd.respond_info("Generating graphs from DEBUG data... (this may take 2-3 minutes)")
                data_snapshot = copy.deepcopy(self.collected_data)
                # Free runtime memory early; we already have a snapshot for plotting
                self.collected_data = []
                self.graph_thread = threading.Thread(
                    target=self._generate_graphs_background,
                    kwargs={
                        'gcmd': gcmd,
                        'graphs_folder': self.graphs_folder,
                        'data_snapshot': data_snapshot,
                        'debug': True,  # DEBUG mode active
                    },
                    daemon=True
                )
                self.graph_thread.start()
                return
            # END DEBUG

            # Heat conditionally
            self._heat_and_wait(gcmd, bed_temp, hotend_temp)
            
            # Home
            gcmd.respond_info("Homing all axes...")
            self.gcode.run_script_from_command("G28")
            self.gcode.run_script_from_command("G90")  # Absolute positioning
            
            # Pre-test calibration (always run)
            self._run_pre_test_calibration(gcmd)

            # Run the grid test (this also collects data)
            self._run_grid_test(gcmd, self.min_x, self.max_x, self.min_y, self.max_y, grid_size, self.default_z_height, max_retries)
            
            # Return to center
            gcmd.respond_info("Returning to center...")
            self.gcode.run_script_from_command(f"G1 Z10 F1000")
            self.gcode.run_script_from_command(f"G1 X{home_x} Y{home_y} F{self.travel_speed}")
            self.toolhead.wait_moves()
            
            # Turn off heaters if they were used
            if bed_temp > 0 or hotend_temp > 0:
                gcmd.respond_info("Turning off heaters...")
                self.gcode.run_script_from_command("M104 S0")
                self.gcode.run_script_from_command("M140 S0")
            
            if self.save_raw_data:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                offset_data_filename = self.raw_data_folder / f"beacon_offset_data_{timestamp}.json"
                try:
                    # Start from existing module metadata only; do not recompute/augment from data.
                    # Add only file format and timestamp.
                    meta_out = dict(self.meta)
                    meta_out.update({
                        "file_version": 1,
                        "test_timestamp": timestamp,
                    })

                    payload = {
                        "meta": meta_out,
                        "data": self.collected_data,
                    }
                    with open(offset_data_filename, 'w') as f:
                        json.dump(payload, f, indent=4)
                    gcmd.respond_info(f"Offset data saved in: {os.path.basename(os.path.dirname(offset_data_filename))}")
                except Exception as e:
                    gcmd.respond_info(f"Warning: failed to save raw offset data: {e}")

            if self.mode == 1:  # 'compensation'
                # Compute and apply axis twist compensation
                gcmd.respond_info("Computing axis twist compensation values...")
                comp_util = AxisTwistCompUtility(self.printer)

                # Define start_x and end_x based on min_x and max_x
                new_start_x = self.min_x
                new_end_x = self.max_x

                gcmd.respond_info("Applying axis twist compensation...")
                new_z_compensations = [data['delta'] for data in self.collected_data]
                comp_util.apply_compensation(new_z_compensations, new_start_x, new_end_x)
                gcmd.respond_info(f"New compensation range: start_x={new_start_x}, end_x={new_end_x}")
                gcmd.respond_info(f"New Z compensations: {new_z_compensations}")
                gcmd.respond_info("Axis twist compensation applied successfully. Please SAVE_CONFIG to apply changes.")


            elif self.mode == 0:  # 'analysis'
                # Generate graphs asynchronously to avoid timer too close errors
                gcmd.respond_info("Generating graphs... (this may take 2-3 minutes)")
                # Make a deep copy of collected_data to avoid race conditions
                data_snapshot = copy.deepcopy(self.collected_data)
                # Free runtime memory early; raw data is saved (if enabled) and snapshot taken
                self.collected_data = []
                self.graph_thread = threading.Thread(
                    target=self._generate_graphs_background,
                    kwargs={
                        'gcmd': gcmd,
                        'graphs_folder': self.graphs_folder,
                        'data_snapshot': data_snapshot,
                        'debug': False,  # Normal run
                    },
                    daemon=True
                )
                self.graph_thread.start()

            else:
                gcmd.respond_info(f"Unknown mode: {self.mode}.")

        except Exception as e:
            self.test_running = False
            raise gcmd.error(f"Test failed: {e}")

        return
