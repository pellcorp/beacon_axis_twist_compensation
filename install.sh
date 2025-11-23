#!/bin/bash

KDIR="${HOME}/klipper"
KENV="${HOME}/klippy-env"
IS_K1_OS=0
if grep -Fqs "ID=buildroot" /etc/os-release; then
  KDIR="/usr/data/klipper"
  KENV="/usr/share/klippy-env"
  IS_K1_OS=1
fi

if [ ! -d "$KDIR" ] || [ ! -d "$KENV" ]; then
    echo "klipper or klippy env doesn't exist"
    exit 1
fi

BKDIR="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"

for file in beacon_axis_twist_compensation.py; do
    if [ -e "${KDIR}/klippy/extras/${file}" ]; then
        rm "${KDIR}/klippy/extras/${file}"
    fi
    ln -s "${BKDIR}/${file}" "${KDIR}/klippy/extras/${file}"
    if ! grep -q "klippy/extras/${file}" "${KDIR}/.git/info/exclude"; then
        echo "klippy/extras/${file}" >> "${KDIR}/.git/info/exclude"
    fi
done
