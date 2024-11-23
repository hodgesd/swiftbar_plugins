#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12

# <xbar.title>ICAO METAR Display with Flight Rules</xbar.title>
# <xbar.version>v1.0</xbar.version>
# <xbar.author>Your Name</xbar.author>
# <xbar.author.github>YourGitHub</xbar.author.github>
# <xbar.desc>Displays ICAO codes color-coded by flight rules from their METAR data using avwx-engine.</xbar.desc>
# <xbar.image>URL_to_an_image_screenshot_of_your_plugin</xbar.image>
# <xbar.dependencies>python,requests,avwx-engine</xbar.dependencies>


from avwx.current.metar import Metar


# Define your ICAO codes here


def airport_wx():
    icao_codes = ["KMCI", "KORD", "KBOS"]

    print("METARs")
    print("---")

    # Define color codes for flight rules
    colors = {
        "VFR": "green",
        "IFR": "red",
        "MVFR": "blue",
        "LIFR": "purple"
    }

    for icao in icao_codes:
        # Fetch METAR data for each ICAO code
        metar = Metar(icao)
        metar.update()

        # Determine the flight rules and corresponding color
        flight_rules = metar.data.flight_rules
        color = colors.get(flight_rules, "gray")  # Default to gray if unknown

        # Construct the URL for the detailed view
        detailed_view_url = f"https://aviationweather.gov/impactboard/?id={icao}&start=0&rwywind=both"

        # Display the ICAO code with color based on flight rules and make it clickable
        print(f"{icao} | color={color} href={detailed_view_url}")
        # Optionally, display the raw METAR data as a submenu item
        print(f"--{metar.data.raw} | font=monospace")


if __name__ == '__main__':
    airport_wx()
