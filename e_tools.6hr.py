#!/Users/hodgesd/PycharmProjects/fsi-dl/venv/bin/python3

# <xbar.title>EM Decision Tools</xbar.title>
# <xbar.version>v1.0</xbar.version>
# <xbar.author>Derrick Hodges</xbar.author>
# <xbar.author.github>majordouble</xbar.author.github>
# <xbar.desc>Automates daily decision tool generation</xbar.desc>
# <xbar.dependencies>python</xbar.dependencies>

import datetime
import os
import subprocess


def send_notification(title, message):
    """Send a notification to macOS Notification Center."""
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], check=True)


print("e")
print("---")
print(f"Last updated: {datetime.datetime.now():%Y-%m-%d %H:%M}")
print(
    "Update now | bash=/Users/hodgesd/PycharmProjects/swiftbar_plugins/.inactive/swiftbar_download_wrapper.12hr.sh terminal=false refresh=true env_update=true")

python_env_path = "/Users/hodgesd/PycharmProjects/fsi-dl/venv/bin/python3"
script_path = "/Users/hodgesd/PycharmProjects/fsi-dl/src/download_and_format_pfm_schedule.py"

# Check if the script was triggered by the 'Update now' button
if os.environ.get('env_update') == 'true':
    try:
        subprocess.check_call([python_env_path, script_path], stderr=subprocess.STDOUT)
        send_notification("EM Decision Tools", "Reports update completed successfully.")

    except subprocess.CalledProcessError as e:
        error_message = f"Error: {e}"
        if e.output:
            error_message += "\nError Output: " + e.output.decode('utf-8')
        print(error_message)
        send_notification("EM Decision Tools", "Update failed. See plugin output for details.")
