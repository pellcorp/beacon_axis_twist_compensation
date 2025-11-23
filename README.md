# Beacon Axis Twist Compensation

Klipper module adjust axis twist compensation for printers running the Beacon Eddy Current RevH model with contact.

See <https://docs.beacon3d.com/commands/#beacon_offset_compare>

## Overview

Compensation mode works out and applies the Z compensation values for Klipper's native gantry twist compensation module `[axis_twist_compensation]`.

## Requirements

- Klipper
- Beacon probe
- SSH access to your printer

## Installation

### K1 Series

```
git clone https://github.com/pellcorp/beacon_axis_twist_compensation.git /usr/data/beacon_axis_twist_compensation
/usr/data/beacon_axis_twist_compensation/install.sh
```

### RPi Series

```
git clone https://github.com/pellcorp/beacon_axis_twist_compensation.git ~/beacon_axis_twist_compensation
~/beacon_axis_twist_compensation/install.sh
```

## Configuration

Add the configuration below to your `printer.cfg` **before** the `SAVE_CONFIG` section, then restart Klipper.

```cfg
[beacon_axis_twist_compensation]
settle_delay: 1.0
point_delay: 1.0
```

It is expected that you will have defined an `[axis_twist_compensation]` config with at least:

```
[axis_twist_compensation]
speed: 50
horizontal_move_z: 5
calibrate_start_x: 30
calibrate_end_x: 220
calibrate_y: 110
```

If you wish to do `AXIS=Y`, you would need to add config like the following:

```
calibrate_start_y: 30
calibrate_end_y: 220
calibrate_x: 110
```

## Usage

```
M104 S150
G28
M109 S150
BEACON_AXIS_TWIST_COMPENSATION SAMPLE_COUNT=3 AXIS=X
```

If no `SAMPLE_COUNT` or `AXIS` is provided, it will run with the config of `3` along `X` axis.

This will:
1. Sample along the X-axis at the center Y position as per config
2. Calculate compensation values
3. Update your `[axis_twist_compensation]` configuration
4. Prompt you to run `SAVE_CONFIG` to persist the changes

### Y Axis

```
M104 S150
G28
M109 S150
BEACON_AXIS_TWIST_COMPENSATION SAMPLE_COUNT=5 AXIS=Y
```

## Credits

Forked and significantly simplified but based on <https://github.com/omgitsgio/gios_gantry_twist_utility>
