# Beacon Grid Test - Klipper Module
#
# Utility to analyse and apply gantry twist compensation using beacon offset data.
#
# Copyright (C) 2025 omgitsgio <gio@omgitsgio.com>
# Copyright (C) 2025 pellcorp <jason@pellcorp.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import gc


class BeaconAxisTwistCompensation:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.configfile = self.printer.lookup_object('configfile')
        self.axis_compensation = self.printer.lookup_object('axis_twist_compensation')
        self.gcode = self.printer.lookup_object('gcode')
        self.name = config.get_name()

        atconfig = config.getsection('axis_twist_compensation')
        if atconfig is None:
            raise self.printer.config_error('Missing axis_twist_compensation config')

        self.min_x = atconfig.getfloat('calibrate_start_x', 22.0, minval=0, maxval=200.0)
        self.max_x = atconfig.getfloat('calibrate_end_x', 283.0, minval=20, maxval=200.0)
        self.min_y = atconfig.getfloat('calibrate_start_y', 22.0, minval=20, maxval=200.0)
        self.max_y = atconfig.getfloat('calibrate_end_y', 283.0, minval=20, maxval=200.0)
        self.calibrate_y = atconfig.getfloat('calibrate_y', None, minval=20.0, maxval=200.0)
        self.calibrate_x = atconfig.getfloat('calibrate_x', None, minval=20.0, maxval=200.0)
        self.default_z_height = atconfig.getfloat('horizontal_move_z', 2.0, minval=1, maxval=5.0)
        self.speed = atconfig.getfloat('speed', 50.0, maxval=150.0) * 100

        self.test_running = False
        self.collected_data = []  # Store all grid test results

        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        
        self.gcode.register_command(
            'BEACON_AXIS_TWIST_COMPENSATION',
            self.cmd_BEACON_AXIS_TWIST_COMPENSATION,
            desc=self.cmd_BEACON_AXIS_TWIST_COMPENSATION_help
        )
    
    def _handle_ready(self):
        """Called when Klipper is ready."""
        try:
            self.beacon = self.printer.lookup_object('beacon')
            self.toolhead = self.printer.lookup_object('toolhead')
        except Exception as e:
            raise self.printer.config_error(
                f"Beacon Axis Twist Compensation requires 'beacon' probe: {e}"
            )

        # this ends up being the home_xy_position
        self.beacon_safe_home_pos = getattr(getattr(self.beacon, 'homing_helper', None), 'home_pos', None)
        if self.beacon_safe_home_pos is None:
            raise self.printer.config_error("Unable to determine beacon safe home position.")
        self.home_x = self.beacon_safe_home_pos[0]
        self.home_y = self.beacon_safe_home_pos[1]

    def _apply_x_compensation(self, new_z_compensations, new_start_x, new_end_x):
        values_as_str = ', '.join(["{:.6f}".format(x) for x in new_z_compensations])
        self.configfile.set('axis_twist_compensation', 'z_compensations', values_as_str)
        self.configfile.set('axis_twist_compensation', 'compensation_start_x', new_start_x)
        self.configfile.set('axis_twist_compensation', 'compensation_end_x', new_end_x)

        # Also update runtime values for immediate effect
        self.axis_compensation.z_compensations = new_z_compensations
        self.axis_compensation.compensation_start_x = new_start_x
        self.axis_compensation.compensation_end_x = new_end_x

    def _apply_y_compensation(self, new_z_compensations, new_start_y, new_end_y):
        values_as_str = ', '.join(["{:.6f}".format(x) for x in new_z_compensations])
        self.configfile.set('axis_twist_compensation', 'new_zy_compensations', values_as_str)
        self.configfile.set('axis_twist_compensation', 'compensation_start_y', new_start_y)
        self.configfile.set('axis_twist_compensation', 'compensation_end_y', new_end_y)

        # Also update runtime values for immediate effect
        self.axis_compensation.zy_compensations = new_z_compensations
        self.axis_compensation.compensation_start_y = new_start_y
        self.axis_compensation.compensation_end_y = new_end_y

    def _generate_grid_points(self, axis, sample_count):
        """Generate grid points for testing based on sampling direction or mode."""
        points = []
        
        # Helper function to calculate position along an axis
        def calc_pos(min_val, max_val, idx, size):
            if size == 1:
                return (min_val + max_val) / 2.0
            else:
                return min_val + (max_val - min_val) * idx / (size - 1)

        if axis == 'X':
            for x_idx in range(sample_count):
                x_pos = calc_pos(self.min_x, self.max_x, x_idx, sample_count)
                points.append((x_pos, self.calibrate_y))
        else:
            for y_idx in range(sample_count):
                y_pos = calc_pos(self.min_x, self.max_x, y_idx, sample_count)
                points.append((self.calibrate_x, y_pos))

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
    
    def _run_grid_test(self, gcmd, axis, sample_count, settle_delay, point_delay):
        """Execute the grid test."""
        # Generate grid points
        points = self._generate_grid_points(axis, sample_count)
        total_points = len(points)
        
        gcmd.respond_info(f"Z height: {self.default_z_height}mm")

        # Execute grid test
        self.test_running = True
        completed = 0
        failed = 0
        
        try:
            for idx, (x_pos, y_pos) in enumerate(points, 1):
                if not self.test_running:
                    gcmd.respond_info("Beacon Axis Twist Compensation cancelled. Exiting...")
                    break
                
                gcmd.respond_info(f"Point {idx}/{total_points}: X{x_pos:.2f} Y{y_pos:.2f}")
                
                try:
                    # Move to position
                    self.gcode.run_script_from_command(
                        f"G1 X{x_pos:.3f} Y{y_pos:.3f} Z{self.default_z_height:.3f} F{self.speed:.0f}"
                    )
                    self.toolhead.wait_moves()
                    
                    self.toolhead.dwell(settle_delay)

                    compare_gcmd = self.gcode.create_gcode_command(
                        "BEACON_OFFSET_COMPARE",
                        "BEACON_OFFSET_COMPARE",
                    )
                    self.beacon.cmd_BEACON_OFFSET_COMPARE(compare_gcmd)
                    self.toolhead.wait_moves()
                    
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
                            'z_commanded': float(self.default_z_height),
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
                    self.toolhead.dwell(point_delay)
                    
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

        gcmd.respond_info(f"Beacon axis compensation points collection complete: {completed}/{total_points} points successful")
        if failed > 0:
            gcmd.respond_info(f"Failed points: {failed}")

    cmd_cmd_BEACON_AXIS_TWIST_COMPENSATION_help = (
        "Beacon Axis Twist Compensation using beacon offset."
    )
    def cmd_BEACON_AXIS_TWIST_COMPENSATION(self, gcmd):
        sample_count = gcmd.get_int('SAMPLE_COUNT', 3, minval=3, maxval=10)
        axis = gcmd.get('AXIS', "X")
        settle_delay = gcmd.getfloat('settle_delay', 1.0)
        point_delay = gcmd.getfloat('point_delay', 1.0)

        try:
            self.printer.lookup_object('axis_twist_compensation')
        except:
            raise gcmd.error("axis_twist_compensation module not found. Please add it to your config")

        if self.test_running:
            raise gcmd.error("Beacon axis twist compensation already running")
        
        self.collected_data = []
        
        try:
            gcmd.respond_info("Homing all axes...")
            self.gcode.run_script_from_command("G28")
            self.gcode.run_script_from_command("G90")  # Absolute positioning

            # Run the grid test (this also collects data)
            self._run_grid_test(gcmd, axis, sample_count, settle_delay, point_delay)

            # Compute and apply axis twist compensation
            gcmd.respond_info("Computing axis twist compensation values...")
            new_z_compensations = [data['delta'] for data in self.collected_data]
            if axis == 'X':
                self._apply_x_compensation(new_z_compensations, self.min_x, self.max_x)
                gcmd.respond_info("Axis twist compensation applied successfully. Please SAVE_CONFIG to apply changes.")
            else:
                self._apply_y_compensation(new_z_compensations, self.min_y, self.max_y)
            gcmd.respond_info("Axis twist compensation applied successfully. Please SAVE_CONFIG to apply changes.")
        except Exception as e:
            self.test_running = False
            raise gcmd.error(f"Test failed: {e}")
        return


def load_config(config):
    return BeaconAxisTwistCompensation(config)
