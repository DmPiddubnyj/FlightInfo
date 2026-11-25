# Swedavia FlightInfo Console

A Python console flight board. It reads live arrivals and departures from Swedavia FlightInfo v2, shows the other airport, airline, local scheduled/estimated/actual times, status, terminal, gate, and baggage belt, and lets you search flights by destination.

## Run

Open a terminal in this folder and run:

```powershell
python airport.py
```

The app reads the Swedavia subscription key from `API key.txt` in this folder. The key is read at runtime and is not written into the Python files or printed by the app. Keep that file private.

## Menu

- `1` Arrivals
- `2` Departures
- `3` Search by destination name or airport code
- `4` Change airport
- `5` Refresh arrivals and departures
- `6` Quit

The board supports all ten Swedavia airports: ARN, BMA, GOT, MMX, LLA, UME, OSD, VBY, RNB, and KRN. Times are converted from UTC to Swedish local time. Long result lists pause every 50 flights.

The app uses only Python's standard library; no package installation is required.

## Daily destination statistics

Run the separate day summary with:

```powershell
python destinationer.py
```

It counts scheduled departures for the current Swedish local date across all ten Swedavia airports. Each country total is split by destination IATA airport code and shows the Swedish airports the flights leave from. To report another date, use `python destinationer.py --date YYYY-MM-DD`.

`city_country.json` is the offline city-to-country lookup built from the destination city list in `destinations-statistik-2025.xlsx`(file is not added to repository, was used only once to create json, in case if it is needed - file is located on swedavia webpage). Any destination the lookup cannot identify is shown under `Country not mapped`, with its airport code, so it is visible for correction.
