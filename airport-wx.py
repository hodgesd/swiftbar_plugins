#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
# <xbar.title>ICAO METAR Display with Flight Rules</xbar.title>
# <xbar.version>v1.2</xbar.version>
# <xbar.author>Your Name</xbar.author>
# <xbar.author.github>YourGitHub</xbar.github>
# <xbar.desc>Displays ICAO codes color-coded by flight rules from their METAR data using avwx-engine.</xbar.desc>
# <xbar.dependencies>python,requests,avwx-engine</xbar.dependencies>

import os
import json
from avwx.current.metar import Metar

# Use SwiftBar's data path for configuration
PLUGIN_DATA_PATH = os.getenv('SWIFTBAR_PLUGIN_DATA_PATH', '')
CONFIG_FILE = os.path.join(os.path.dirname(PLUGIN_DATA_PATH), 'metar_airports.json')

def load_icao_codes():
    """Load ICAO codes from config file."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return ["KMCI", "KORD", "KBOS"]  # Default airports if no config exists

def save_icao_codes(codes):
    """Save ICAO codes to config file."""
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(codes, f)

def add_airport(icao):
    """Add a new airport to the config."""
    codes = load_icao_codes()
    icao = icao.strip().upper()
    if icao and icao not in codes:
        try:
            # Verify the ICAO code is valid by attempting to fetch METAR
            Metar(icao).update()
            codes.append(icao)
            save_icao_codes(codes)
            return True
        except Exception as e:
            print(f"Error adding {icao}: {str(e)}")
            return False
    return False

def remove_airport(icao):
    """Remove an airport from the config."""
    codes = load_icao_codes()
    if icao in codes:
        codes.remove(icao)
        save_icao_codes(codes)

def airport_wx():
    icao_codes = load_icao_codes()

    colors = {
        "VFR": "green",
        "IFR": "red",
        "MVFR": "blue",
        "LIFR": "purple"
    }

    print("✈️ METARs")
    print("---")

    # Display current airports
    for icao in icao_codes:
        try:
            metar = Metar(icao)
            metar.update()
            flight_rules = metar.data.flight_rules
            color = colors.get(flight_rules, "gray")
            detailed_view_url = f"https://aviationweather.gov/impactboard/?id={icao}&start=0&rwywind=both"

            print(f"{icao} | color={color} href={detailed_view_url}")
            print(f"--{metar.data.raw} | font=monospace")
            print(f"--Remove {icao} | bash='{os.path.realpath(__file__)}' param1='remove' param2='{icao}' terminal=false refresh=true")
        except Exception as e:
            print(f"{icao} (Error) | color=red")
            print(f"--Error: {str(e)} | color=red")

    # Add airport section
    print("---")
    print("Add Airport")
    nearby_airports = ["KSTL", "KBLV", "KCPS", "KALN", "KSET"]  # Modified for St. Louis area
    for airport in nearby_airports:
        if airport not in icao_codes:
            print(f"--Add {airport} | bash='{os.path.realpath(__file__)}' param1='add' param2='{airport}' terminal=false refresh=true")

    # Simple command to add custom airport
    print(f"--Add Custom Airport... | bash='{os.path.realpath(__file__)}' param1='add_custom' terminal=true refresh=true")
    #
    # # print stand along notams for KCPS
    # notam = Notams('KCPS')
    # notam.update()
    # # Access raw data
    # print("Raw NOTAMs:")
    # for single_notam in notam.data:
    #     print("\nNOTAM Details:")
    #     print(f"ID: {single_notam.number}")
    #     print(f"Type: {single_notam.type}")
    #     print(f"Start Time: {single_notam.start_time}")
    #     print(f"End Time: {single_notam.end_time}")
    #     print(f"Body: {single_notam.body}")
    #
    #     # If you want to see the complete raw message
    #     print(f"Raw Text: {single_notam.raw}")
    #
    #     # Access decoded/translated message if available
    #     if hasattr(single_notam, 'decoded'):
    #         print(f"Decoded: {single_notam.decoded}")
    #
    # # You can also get a summary report
    # if hasattr(notam, 'summary'):
    #     print("\nSummary:")
    #     print(notam.summary)
    #
    # # To see all available attributes
    # print("\nAll available attributes:")
    # for attr in dir(notam.data[0]):
    #     if not attr.startswith('_'):  # Skip internal attributes
    #         print(attr)


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == 'add' and len(sys.argv) > 2:
            add_airport(sys.argv[2])
        elif command == 'remove' and len(sys.argv) > 2:
            remove_airport(sys.argv[2])
        elif command == 'add_custom':
            print("Enter ICAO code (e.g., KSTL):")
            icao = input().strip().upper()
            if add_airport(icao):
                print(f"Successfully added {icao}")
            else:
                print(f"Failed to add {icao}")
            input("Press Enter to close...")
    else:
        airport_wx()
