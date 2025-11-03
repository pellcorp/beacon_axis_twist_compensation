# Axis Twist Compensation Utility - Klipper Module
#
# Small module called by gantry_twist_analysis to handle applying axis twist compensation values to the config
#
# Copyright (C) 2025 omgitsgio <gio@omgitsgio.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

class AxisTwistCompUtility:
    def __init__(self, printer):
        self.printer = printer
        self.configfile = self.printer.lookup_object('configfile')
        self.axis_compensation = self.printer.lookup_object('axis_twist_compensation')

    def apply_compensation(self, new_z_compensations, new_start_x, new_end_x):
        # Apply the computed compensation to the given data
        # Stage changes (requires SAVE_CONFIG to persist)
        values_as_str = ', '.join(["{:.6f}".format(x) for x in new_z_compensations])
        self.configfile.set('axis_twist_compensation', 'z_compensations', values_as_str)
        self.configfile.set('axis_twist_compensation', 'compensation_start_x', new_start_x)
        self.configfile.set('axis_twist_compensation', 'compensation_end_x', new_end_x)

        # Also update runtime values for immediate effect
        self.axis_compensation.z_compensations = new_z_compensations
        self.axis_compensation.compensation_start_x = new_start_x
        self.axis_compensation.compensation_end_x = new_end_x
