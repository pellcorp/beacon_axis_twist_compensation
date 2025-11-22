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
from pathlib import Path
import copy
import gc
from .axis_twist_comp_utility import AxisTwistCompUtility


class GantryTwistUtility:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.name = config.get_name()
        
        # Configuration - defaults for the grid
        self.min_x = config.getfloat('calibrate_start_x', 22.0, minval=0, maxval=280.0) # min y = 20 max y = 300
        self.max_x = config.getfloat('calibrate_end_x', 283.0, minval=20, maxval=300.0)
        self.min_y = config.getfloat('calibrate_start_y', 22.0, minval=20, maxval=300.0)
        self.max_y = config.getfloat('calibrate_end_y', 283.0, minval=20, maxval=300.0)
        self.comp_y_position = config.getfloat('calibrate_y', None, minval=20.0, maxval=300.0)
        self.grid_size = config.getint('grid_size', 10, minval=2)
        self.default_z_height = config.getfloat('horizontal_move_z', 2.0, minval=1, maxval=5.0)
        self.settle_delay = config.getfloat('settle_delay', 1.0)
        self.point_delay = config.getfloat('point_delay', 1.0)
        self.travel_speed = config.getfloat('travel_speed', 5000.0, maxval=20000.0)

        self.meta = {}

        # State
        self.test_running = False
        self.collected_data = []  # Store all grid test results

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

    def _generate_grid_points(self, gcmd, min_x, max_x, grid_size):
        """Generate grid points for testing based on sampling direction or mode."""
        points = []
        
        # Helper function to calculate position along an axis
        def calc_pos(min_val, max_val, idx, size):
            if size == 1:
                return (min_val + max_val) / 2.0
            else:
                return min_val + (max_val - min_val) * idx / (size - 1)

        # Mode 1 (compensation) samples X-axis only at center Y
        for x_idx in range(grid_size):
            x_pos = calc_pos(min_x, max_x, x_idx, grid_size)
            points.append((x_pos, self.comp_y_position))

        total_points = len(points)
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
    
    def _run_grid_test(self, gcmd, min_x, max_x, grid_size, z_height):
        """Execute the grid test."""
        # Generate grid points
        points = self._generate_grid_points(gcmd, min_x, max_x, grid_size)
        
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

                    # the beacon offset compare uses the autocal_sample_count for retries
                    compare_gcmd = self.gcode.create_gcode_command(
                        "BEACON_OFFSET_COMPARE",
                        "BEACON_OFFSET_COMPARE",
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
        
        gcmd.respond_info(f"Axis compensation points collection complete: {completed}/{total_points} points successful")
        if failed > 0:
            gcmd.respond_info(f"Failed points: {failed}")
        
        gcmd.respond_info("=" * 60)

    cmd_GANTRY_TWIST_UTILITY_help = (
        "Run the gantry twist utility using beacon offset."
    )
    def cmd_GANTRY_TWIST_UTILITY(self, gcmd):
        """Entry point for GANTRY_TWIST_UTILITY GCode command."""

        # Check if we have the axis_twist_compensation module      
        try:
            self.printer.lookup_object('axis_twist_compensation')
        except:
            raise gcmd.error("axis_twist_compensation module not found. Please add it to your config")

        home_x = (self.max_x + self.min_x) / 2.0
        home_y = self.comp_y_position

        if self.test_running:
            raise gcmd.error("Grid test already running")
        
        # Clear previous data
        self.collected_data = []
        
        gcmd.respond_info("=" * 60)
        gcmd.respond_info("  GANTRY TWIST UTILITY  ")
        gcmd.respond_info("=" * 60)
        
        try:
            gcmd.respond_info("Homing all axes...")
            self.gcode.run_script_from_command("G28")
            self.gcode.run_script_from_command("G90")  # Absolute positioning

            # Run the grid test (this also collects data)
            self._run_grid_test(gcmd, self.min_x, self.max_x, self.grid_size, self.default_z_height)

            # Compute and apply axis twist compensation
            gcmd.respond_info("Computing axis twist compensation values...")
            comp_util = AxisTwistCompUtility(self.printer)

            gcmd.respond_info("Applying axis twist compensation...")
            new_z_compensations = [data['delta'] for data in self.collected_data]
            comp_util.apply_compensation(new_z_compensations, self.min_x, self.max_x)
            gcmd.respond_info(f"New compensation range: start_x={self.min_x}, end_x={self.max_x}")
            gcmd.respond_info(f"New Z compensations: {new_z_compensations}")
            gcmd.respond_info("Axis twist compensation applied successfully. Please SAVE_CONFIG to apply changes.")

        except Exception as e:
            self.test_running = False
            raise gcmd.error(f"Test failed: {e}")

        return
