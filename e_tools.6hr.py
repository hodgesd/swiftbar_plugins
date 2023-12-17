#!/Users/hodgesd/PycharmProjects/fsi-dl/venv/bin/python3

# <xbar.title>EM Decision Tools</xbar.title>
# <xbar.version>v1.0</xbar.version>
# <xbar.author>Derrick Hodges</xbar.author>
# <xbar.author.github>majordouble</xbar.author.github>
# <xbar.desc>Automates daily decision tool generation</xbar.desc>
# <xbar.dependencies>python</xbar.dependencies>

import datetime
import subprocess

print("e")
print("---")
print(f"Last updated: {datetime.datetime.now()}")
print(
    "Update now | bash=/Users/hodgesd/PycharmProjects/fsi-dl/swiftbar_download_wrapper.12hr.sh terminal=false refresh=true")

python_env_path = "/Users/hodgesd/PycharmProjects/fsi-dl/venv/bin/python3"
script_path = "/Users/hodgesd/PycharmProjects/fsi-dl/src/download_and_format_pfm_schedule.py"

try:
    subprocess.check_call([python_env_path, script_path], stderr=subprocess.STDOUT)
except subprocess.CalledProcessError as e:
    print(f"Error: {e}")
    if e.output:
        print("Error Output:", e.output.decode('utf-8'))
