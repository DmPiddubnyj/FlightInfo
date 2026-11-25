"""Daily departure totals grouped by destination country and airport."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from airport import AIRPORTS, FlightInfoError, _get, _nested, _parse_utc, fetch_flights

COUNTRY_FILE = Path(__file__).with_name("city_country.json")

# The FlightInfo feed can use Swedish place names while the offline reference
# uses English names. Keep these variants here so the report still works offline.
CITY_ALIASES = {
    "kopenhamn": "copenhagen",
    "kobenhavn": "copenhagen",
    "helsingfors": "helsinki",
    "prag": "prague",
    "warszawa": "warsaw",
    "rom": "rome",
    "aten": "athens",
    "lissabon": "lisbon",
    "moskva": "moscow",
    "milano": "milan",
    "munich": "munchen",
    "wien": "vienna",
    "parischarlesdegaulle": "paris",
    "londonheathrow": "london",
    "londongatwick": "london",
}

CITY_COUNTRY_OVERRIDES = {
    "munich": "Germany",
    "vaasa": "Finland",
    "sundsvall": "Sweden",
    "malta": "Malta",
    "addisabeba": "Ethiopia",
    "hemavantarnaby": "Sweden",
    "bergamo": "Italy",
    "podgorica": "Montenegro",
}

IATA_COUNTRY_OVERRIDES = {
    "MUC": "Germany",
    "VAA": "Finland",
    "SDL": "Sweden",
    "MLA": "Malta",
    "ADD": "Ethiopia",
    "HMV": "Sweden",
    "BGY": "Italy",
    "TGD": "Montenegro",
}

COUNTRY_DISPLAY_NAMES = {
    "great britain": "United Kingdom",
    "irish republic": "Ireland",
    "bosnia-herzegovina": "Bosnia and Herzegovina",
    "united states of america": "United States",
}


def normalise(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in value if not unicodedata.combining(char) and char.isalnum())


def load_country_lookup() -> dict[str, Any]:
    if not COUNTRY_FILE.is_file():
        raise FlightInfoError(f"Country lookup not found: {COUNTRY_FILE.name}.")
    try:
        payload = json.loads(COUNTRY_FILE.read_text(encoding="utf-8"))
        lookup = payload.get("countries_by_city", {})
        if not isinstance(lookup, dict):
            raise ValueError
        return lookup
    except (OSError, json.JSONDecodeError, ValueError):
        raise FlightInfoError(f"Could not read {COUNTRY_FILE.name}.") from None


def city_candidates(name: str) -> list[str]:
    raw = name.strip()
    if not raw:
        return []
    candidates = [normalise(raw)]
    # Some FlightInfo names include an IATA code, for example "London LHR".
    without_code = re.sub(r"[\s,(/-]+[A-Z]{3}\)?$", "", raw, flags=re.I).strip()
    if without_code and without_code != raw:
        candidates.append(normalise(without_code))
    # Ignore terminal/airport suffixes when the country reference only lists a city.
    for suffix in (" airport", " international", " international airport"):
        if raw.casefold().endswith(suffix):
            candidates.append(normalise(raw[: -len(suffix)]))
    expanded = []
    for candidate in candidates:
        expanded.append(candidate)
        alias = CITY_ALIASES.get(candidate)
        if alias:
            expanded.append(alias)
    return list(dict.fromkeys(expanded))


def find_country(name: str, airport_code: str, lookup: dict[str, Any]) -> str | None:
    airport_code = airport_code.upper()
    if airport_code in AIRPORTS:
        return "Sweden"
    if airport_code in IATA_COUNTRY_OVERRIDES:
        return IATA_COUNTRY_OVERRIDES[airport_code]
    for candidate in city_candidates(name):
        value = lookup.get(candidate)
        if isinstance(value, str):
            return COUNTRY_DISPLAY_NAMES.get(value.casefold(), value)
        if candidate in CITY_COUNTRY_OVERRIDES:
            value = CITY_COUNTRY_OVERRIDES[candidate]
            return COUNTRY_DISPLAY_NAMES.get(value.casefold(), value)
        # An ambiguous city is intentionally left unmapped.
        if isinstance(value, list):
            return None
    return None


def utc_dates_for_local_day(local_day: date) -> list[date]:
    start_local = datetime.combine(local_day, time.min).astimezone()
    end_local = datetime.combine(local_day + timedelta(days=1), time.min).astimezone()
    first_utc_day = start_local.astimezone(timezone.utc).date()
    last_utc_day = (end_local - timedelta(microseconds=1)).astimezone(timezone.utc).date()
    return [first_utc_day + timedelta(days=offset) for offset in range((last_utc_day - first_utc_day).days + 1)]


def flight_destination(flight: dict[str, Any]) -> tuple[str, str]:
    identifier = _nested(flight, "FlightLegIdentifier")
    code = str(
        _get(identifier, "ArrivalAirportIata", default="")
        or _get(flight, "ArrivalAirportIata", default="")
        or ""
    ).strip().upper()
    name = str(
        _get(flight, "ArrivalAirportEnglish", default="")
        or _get(flight, "ArrivalAirportSwedish", default="")
        or _get(flight, "ArrivalAirport", default="")
        or ""
    ).strip()
    return code or "???", name or "Unknown destination"


def collect_departures(local_day: date, lookup: dict[str, Any]):
    utc_days = utc_dates_for_local_day(local_day)
    groups: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    unmapped: Counter[tuple[str, str]] = Counter()
    failures: list[str] = []
    missing_times = 0
    total = 0
    requests_ok = 0
    seen: set[tuple[str, str, str, str]] = set()

    for origin_code, origin_name in AIRPORTS.items():
        print(f"Fetching departures from {origin_name} ({origin_code}) ...")
        for utc_day in utc_days:
            try:
                flights, _ = fetch_flights(origin_code, "departures", utc_day)
                requests_ok += 1
            except FlightInfoError as exc:
                failures.append(f"{origin_code} for UTC date {utc_day}: {exc}")
                continue

            for flight in flights:
                time_data = _nested(flight, "DepartureTime")
                scheduled_raw = _get(time_data, "ScheduledUtc", default="")
                scheduled_local = _parse_utc(scheduled_raw)
                if not scheduled_local:
                    missing_times += 1
                    continue
                if scheduled_local.date() != local_day:
                    continue

                destination_code, destination_name = flight_destination(flight)
                flight_id = str(_get(flight, "FlightId", default="") or "").strip()
                unique_key = (origin_code, flight_id, destination_code, str(scheduled_raw))
                if unique_key in seen:
                    continue
                seen.add(unique_key)

                country = find_country(destination_name, destination_code, lookup)
                if country is None:
                    country = "Country not mapped"
                    unmapped[(destination_code, destination_name)] += 1
                bucket = groups[country].setdefault(
                    destination_code,
                    {"name": destination_name, "total": 0, "origins": Counter()},
                )
                bucket["total"] += 1
                bucket["origins"][origin_code] += 1
                total += 1

    return groups, unmapped, failures, missing_times, total, requests_ok, len(AIRPORTS) * len(utc_days)


def print_report(local_day: date, result: tuple[Any, ...]) -> None:
    groups, unmapped, failures, missing_times, total, requests_ok, requests_total = result
    print("\nSWEDAVIA DEPARTURE COUNTRY STATISTICS")
    print(f"Departure date: {local_day:%Y-%m-%d} (Swedish local date)")
    print(f"Swedish departure airports: {len(AIRPORTS)}")
    print(f"Flights counted: {total}")
    print(f"API requests completed: {requests_ok}/{requests_total}")
    if failures:
        print("DATA INCOMPLETE: one or more airport/date requests failed.")

    if not groups:
        print("No departures were returned for this date.")
    else:
        for country in sorted(groups, key=lambda name: (name == "Country not mapped", name.casefold())):
            destinations = groups[country]
            country_total = sum(item["total"] for item in destinations.values())
            print(f"\n{country.upper()}  |  {country_total} flights")
            for code, item in sorted(destinations.items(), key=lambda entry: (-entry[1]["total"], entry[0])):
                origins = ", ".join(
                    f"{origin}: {count}"
                    for origin, count in sorted(item["origins"].items(), key=lambda pair: pair[0])
                )
                print(f"  {code:4} {item['name']:<30} {item['total']:>4}  |  From Sweden: {origins}")

    if unmapped:
        print(f"\nCountry lookup missing for {len(unmapped)} destination(s):")
        for (code, name), count in sorted(unmapped.items(), key=lambda pair: (-pair[1], pair[0][0]))[:25]:
            print(f"  {code} {name} ({count} flights)")
        if len(unmapped) > 25:
            print(f"  ... and {len(unmapped) - 25} more")
    if missing_times:
        print(f"\nSkipped {missing_times} flight record(s) without a scheduled time.")
    if failures:
        print("\nRequest errors:")
        for failure in failures:
            print(f"  {failure}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Count today's Swedavia departures by destination country.")
    parser.add_argument("--date", type=date.fromisoformat, help="Swedish local date in YYYY-MM-DD format (defaults to today).")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    local_day = args.date or datetime.now().astimezone().date()
    try:
        lookup = load_country_lookup()
        result = collect_departures(local_day, lookup)
        print_report(local_day, result)
        return 1 if result[2] else 0
    except FlightInfoError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
