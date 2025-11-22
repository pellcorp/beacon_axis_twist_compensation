# Beacon Grid Test - Klipper Module
#
# Utility to analyse and apply gantry twist compensation using beacon offset data.
#
# Copyright (C) 2025 omgitsgio <gio@omgitsgio.com>
# Copyright (C) 2025 pellcorp <jason@pellcorp.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

DEFAULT_SAMPLE_COUNT = 3
DEFAULT_SPEED = 50.
DEFAULT_HORIZONTAL_MOVE_Z = 5.


class BeaconAxisTwistCompensation:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.configfile = self.printer.lookup_object('configfile')
        self.axis_compensation = self.printer.lookup_object('axis_twist_compensation')
        self.gcode = self.printer.lookup_object('gcode')
        self.name = config.get_name()
        self.beacon = None
        self.lift_speed = None

        atconfig = config.getsection('axis_twist_compensation')
        if atconfig is None:
            raise self.printer.config_error('Missing axis_twist_compensation config')

        self.settle_delay = config.getfloat('settle_delay', 1.0)
        self.point_delay = config.getfloat('point_delay', 1.0)

        # get values from [axis_twist_compensation] section in printer .cfg
        self.horizontal_move_z = atconfig.getfloat('horizontal_move_z',
                                                 DEFAULT_HORIZONTAL_MOVE_Z)
        self.speed = atconfig.getfloat('speed', DEFAULT_SPEED)
        self.calibrate_start_x = atconfig.getfloat('calibrate_start_x',
                                                 default=None)
        self.calibrate_end_x = atconfig.getfloat('calibrate_end_x', default=None)
        self.calibrate_y = atconfig.getfloat('calibrate_y', default=None)
        self.compensation_start_x = atconfig.getfloat('compensation_start_x',
                                                    default=None)
        self.compensation_end_x = atconfig.getfloat('compensation_end_x',
                                                  default=None)

        self.calibrate_start_y = atconfig.getfloat('calibrate_start_y',
                                                 default=None)
        self.calibrate_end_y = atconfig.getfloat('calibrate_end_y', default=None)
        self.calibrate_x = atconfig.getfloat('calibrate_x', default=None)
        self.compensation_start_y = atconfig.getfloat('compensation_start_y',
                                                    default=None)
        self.compensation_end_y = atconfig.getfloat('compensation_end_y',
                                                  default=None)

        self.x_start_point = (self.calibrate_start_x,
                              self.calibrate_y)
        self.x_end_point = (self.calibrate_end_x,
                            self.calibrate_y)
        self.y_start_point = (self.calibrate_x,
                              self.calibrate_start_y)
        self.y_end_point = (self.calibrate_x,
                            self.calibrate_end_y)

        self.test_running = False
        self.results = []

        self.printer.register_event_handler("klippy:connect",
                                   self._handle_connect)

        self.gcode.register_command(
            'BEACON_AXIS_TWIST_COMPENSATION',
            self.cmd_BEACON_AXIS_TWIST_COMPENSATION,
            desc=self.cmd_BEACON_AXIS_TWIST_COMPENSATION_help
        )


    def _handle_connect(self):
        config = self.printer.lookup_object('configfile')
        self.beacon = self.printer.lookup_object('beacon')
        if self.beacon is None:
            raise config.error(
                f"BEACON_AXIS_TWIST_COMPENSATION requires a 'beacon' probe"
            )
        self.toolhead = self.printer.lookup_object('toolhead')
        self.lift_speed = getattr(self.beacon, 'lift_speed', None)

        self.compare_gcmd = self.gcode.create_gcode_command(
            "BEACON_OFFSET_COMPARE",
            "BEACON_OFFSET_COMPARE",
            {}
        )

    def _apply_compensations(self, axis, new_z_compensations):
        values_as_str = ', '.join(["{:.6f}".format(x) for x in new_z_compensations])
        if axis == 'X':
            self.configfile.set('axis_twist_compensation', 'z_compensations', values_as_str)
            self.configfile.set('axis_twist_compensation', 'compensation_start_x', self.x_start_point[0])
            self.configfile.set('axis_twist_compensation', 'compensation_end_x', self.x_end_point[0])

            # Also update runtime values for immediate effect
            self.axis_compensation.z_compensations = new_z_compensations
            self.axis_compensation.compensation_start_x = self.x_start_point[0]
            self.axis_compensation.compensation_end_x = self.x_end_point[0]
        else:
            self.configfile.set('axis_twist_compensation', 'new_zy_compensations', values_as_str)
            self.configfile.set('axis_twist_compensation', 'compensation_start_y', self.y_start_point[1])
            self.configfile.set('axis_twist_compensation', 'compensation_end_y', self.y_end_point[1])

            # Also update runtime values for immediate effect
            self.axis_compensation.zy_compensations = new_z_compensations
            self.axis_compensation.compensation_start_y = self.y_start_point[1]
            self.axis_compensation.compensation_end_y = self.y_end_point[1]


    def _calibration(self, nozzle_points):
        total_points = len(nozzle_points)

        self.test_running = True

        try:
            for idx, (x_pos, y_pos) in enumerate(nozzle_points, 1):
                if not self.test_running:
                    self.gcmd.respond_info("Beacon Axis Twist Compensation cancelled. Exiting...")
                    break

                self.gcmd.respond_info(f"Point {idx}/{total_points}: X{x_pos:.2f} Y{y_pos:.2f}")
                
                # Move to position
                self._move_helper((x_pos, y_pos, self.horizontal_move_z), self.speed)
                self.toolhead.wait_moves()
                self.toolhead.dwell(self.settle_delay)

                self.beacon.cmd_BEACON_OFFSET_COMPARE(self.compare_gcmd)
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
                        'z_commanded': float(self.horizontal_move_z),
                        'contact_z': contact_z,
                        'proximity_z': proximity_z,
                        'delta': delta,
                        'delta_um': delta * 1000.0,
                    }
                    self.results.append(data_point)
                else:
                    # it makes no sense to me to allow a partial result its either all or nothing
                    raise self.gcmd.error("No beacon offset result data available.")

                self.toolhead.dwell(self.point_delay)
        finally:
            self.test_running = False

        self.gcmd.respond_info(f"Beacon axis twist compensation complete")


    # taken straight from axis_twist_compensation
    def _move_helper(self, target_coordinates, override_speed=None):
        # pad target coordinates
        target_coordinates = \
            (target_coordinates[0], target_coordinates[1], None) \
                if len(target_coordinates) == 2 else target_coordinates
        speed = self.speed if target_coordinates[2] == None else self.lift_speed
        speed = override_speed if override_speed is not None else speed
        self.toolhead.manual_move(target_coordinates, speed)


    cmd_BEACON_AXIS_TWIST_COMPENSATION_help = (
        "Beacon Axis Twist Compensation using beacon offset."
    )
    def cmd_BEACON_AXIS_TWIST_COMPENSATION(self, gcmd):
        self.gcmd = gcmd
        sample_count = self.gcmd.get_int('SAMPLE_COUNT', 3, minval=3, maxval=10)
        axis = self.gcmd.get('AXIS', "X")

        # check for valid sample_count
        if sample_count < 2:
            raise self.gcmd.error(
                "SAMPLE_COUNT to probe must be at least 2")

        if self.test_running:
            raise self.gcmd.error("Beacon axis twist compensation already running")

        mozzle_points = []
        self.results = []

        if axis == 'X':
            if not all([
                self.x_start_point[0],
                self.x_end_point[0],
                self.x_start_point[1]
            ]):
                raise self.gcmd.error(
                    """Beacon axis twist compensation for X axis requires
                    calibrate_start_x, calibrate_end_x and calibrate_y
                    to be defined
                    """
                )

            start_point = self.x_start_point
            end_point = self.x_end_point

            x_axis_range = end_point[0] - start_point[0]
            interval_dist = x_axis_range / (sample_count - 1)

            for i in range(sample_count):
                x = start_point[0] + i * interval_dist
                y = start_point[1]
                mozzle_points.append((x, y))
        elif axis == 'Y':
            if not all([
                self.y_start_point[0],
                self.y_end_point[0],
                self.y_start_point[1]
            ]):
                raise self.gcmd.error(
                    """Beacon axis twist compensation for Y axis requires
                    calibrate_start_y, calibrate_end_y and calibrate_x
                    to be defined
                    """
                )

            start_point = self.y_start_point
            end_point = self.y_end_point

            y_axis_range = end_point[1] - start_point[1]
            interval_dist = y_axis_range / (sample_count - 1)

            for i in range(sample_count):
                x = start_point[0]
                y = start_point[1] + i * interval_dist
                mozzle_points.append((x, y))
        else:
            raise self.gcmd.error(
                "Beacon axis twist compensation: "
                "Invalid axis.")

        try:
            self._calibration(mozzle_points)
            if len(self.collected_data) > 0:
                # Compute and apply axis twist compensation
                gcmd.respond_info("Computing Beacon Axis twist compensation values...")
                new_z_compensations = [data['delta'] for data in self.results]
                self._apply_compensations(axis, new_z_compensations)
                gcmd.respond_info("Beacon Axis twist compensation applied successfully. Please SAVE_CONFIG to apply changes.")
        except Exception as e:
            self.test_running = False
            raise gcmd.error(f"Beacon Axis twist compensation failed: {e}")
        return


def load_config(config):
    return BeaconAxisTwistCompensation(config)
