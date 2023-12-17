#!/bin/bash


# Metadata allows your plugin to show up in the app, and website.
#
#  <xbar.title>EM Decision Tools</xbar.title>
#  <xbar.version>v1.0</xbar.version>
#  <xbar.author>Derrick Hodges</xbar.author>
#  <xbar.author.github>majordouble</xbar.author.github>
#  <xbar.desc>Automates daily decision tool generation</xbar.desc>
#  <xbar.dependencies>python</xbar.dependencies>

#SF_SYMBOLS_CHART='/var/folders/ym/f089vk_d3plfrbjh2vk2lz3h0000gn/T/TemporaryItems/NSIRD_SF Symbols_aMSwWj/chart.dots.scatter.png'
SF_SYMBOLS_CHART'📊'

echo "e"
#echo "| image=$SF_SYMBOLS_CHART"
echo "---"
echo "Last updated: $(date)"
echo "Update now | bash=/Users/hodgesd/PycharmProjects/fsi-dl/swiftbar_download_wrapper.12hr.sh terminal=false refresh=true"

# Activate your Python environment if necessary
source /Users/hodgesd/PycharmProjects/fsi-dl/venv/bin/activate

# Run your Python script
cd /Users/hodgesd/PycharmProjects/fsi-dl
#python download_and_format_pfm_schedule.py
python /Users/hodgesd/PycharmProjects/fsi-dl/src/download_and_format_pfm_schedule.py