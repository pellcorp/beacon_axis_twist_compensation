# Gio's Gantry Twist Utility

Klipper module to visualize and adjust gantry twist for printers running the Beacon Eddy Current Surface Scanner

## Overview

Compensation mode works out and applies the Z compensation values for Klipper's native gantry twist compensation module `[axis_twist_compensation]`.

### Requirements

- Klipper
- Beacon probe
- SSH access to your printer

### Compatibility

This module can work with any printer running Klipper but was designed and tested on a QIDI Plus 4. As such, the installation guide and the default settings are tailored for it. If you install/run on a different printer, please review the installation and settings carefully.

Add the configuration below to your `printer.cfg` **before** the `SAVE_CONFIG` section, then restart Klipper.

```cfg
[gantry_twist_utility]

# Mesh boundaries to probe. When in compensation mode, X values will be used for start_x and end_x.
# min_x: 22.0
# max_x: 283.0
# min_y: 22.0
# max_y: 283.0
# calibrate_y: 152.5

# Points per axis (grid_size * grid_size).
# When in compensation mode, this will be the sample size along X-axis.
# grid_size: 10

# Test temps
# bed_temp: 0.0
# hotend_temp: 0.0
```
For more configuration options please refer to [sample_config_complete.cfg](sample_config_complete.cfg).

## Usage

Just send to your console:

```
GANTRY_TWIST_UTILITY
```

If no settings are declared, it will run with the config above by default (hence in analysis mode).

You can specify some arguments from console which will override the config file:
```
MODE, BED_TEMP, HOTEND_TEMP, GRID_SIZE, MAX_RETRIES, CALIBRATE_Y
```

### Compensation

To automatically calculate and apply axis twist compensation values:

```
GANTRY_TWIST_UTILITY
```

This will:
1. Sample along the X-axis at the center Y position as per config
2. Calculate compensation values
3. Update your `[axis_twist_compensation]` configuration
4. Prompt you to run `SAVE_CONFIG` to persist the changes

## Credits

A lot of the problem-solving was possible by taking inspiration from <https://github.com/Frix-x/klippain-shaketune>.
