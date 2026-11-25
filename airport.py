"""Console flight board for Swedavia's FlightInfo v2 API."""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://api.swedavia.se/flightinfo/v2"
KEY_FILE = Path(__file__).with_name("API key.txt")
PAGE_SIZE = 50
REQUEST_TIMEOUT = 20
STATUS_NAMES = {
    "SCH": "Scheduled",
    "FPL": "Flight plan",
    "FLS": "Suspended",
    "SEQ": "Sequenced",
    "ACT": "Active",
    "CAN": "Cancelled",
    "LAN": "Landed",
    "RER": "Rerouted",
    "DIV": "Diverted",
    "DEL": "Deleted",
}

AIRPORTS = {
    "ARN": "Stockholm Arlanda",
    "BMA": "Stockholm Bromma",
    "GOT": "Göteborg Landvetter",
    "MMX": "Malmö",
    "LLA": "Luleå",
    "UME": "Umeå",
    "OSD": "Åre Östersund",
    "VBY": "Visby",
    "RNB": "Ronneby",
    "KRN": "Kiruna",
}


class FlightInfoError(Exception):
    """A readable error returned by the FlightInfo client."""


def read_api_key() -> str:
    """Read the subscription key from the supplied local file, without echoing it."""
    if not KEY_FILE.is_file():
        raise FlightInfoError(
            f"API key file not found: {KEY_FILE.name}. Put the Swedavia key in that file."
        )
    raw = KEY_FILE.read_text(encoding="utf-8-sig").strip()
    for line in raw.splitlines():
        value = line.strip().strip('"\'')
        value = re.sub(r"^(?:api\s*key|primary\s*key|subscription\s*key)\s*[:=]\s*", "", value, flags=re.I)
        if value:
            return value
    raise FlightInfoError(f"No API key was found in {KEY_FILE.name}.")


def api_request(path: str) -> tuple[dict[str, Any], dict[str, str]]:
    key = read_api_key()
    request = Request(
        f"{BASE_URL}/{path.lstrip('/')}",
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Accept": "application/json",
            "User-Agent": "SwedaviaFlightInfoConsole/1.0",
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8-sig"))
            return payload, {name.lower(): value for name, value in response.headers.items()}
    except HTTPError as exc:
        if exc.code in (401, 403):
            message = "Swedavia rejected the key. Check that API key.txt contains the active FlightInfo key."
        elif exc.code == 429:
            message = "Swedavia's request limit was reached. Wait before refreshing."
        else:
            message = f"Swedavia returned HTTP {exc.code}."
        raise FlightInfoError(message) from None
    except (URLError, TimeoutError, OSError):
        raise FlightInfoError("Could not reach Swedavia. Check the internet connection and try again.") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FlightInfoError("Swedavia returned a response the app could not read.") from None


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _get(mapping: Any, *names: str, default: Any = "") -> Any:
    if not isinstance(mapping, dict):
        return default
    wanted = {_norm(name) for name in names}
    for key, value in mapping.items():
        if _norm(str(key)) in wanted and value is not None:
            return value
    return default


def _nested(mapping: Any, *names: str) -> dict[str, Any]:
    value = _get(mapping, *names, default={})
    return value if isinstance(value, dict) else {}


def _flight_list(payload: Any) -> list[dict[str, Any]]:
    """Accept the documented envelope and common API gateway list wrappers."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key, value in payload.items():
        if _norm(str(key)) in {"flights", "value", "items"} and isinstance(value, list):
            if not value or isinstance(value[0], dict):
                return value
    return []


def fetch_flights(airport: str, direction: str, flight_date: date | None = None) -> tuple[list[dict[str, Any]], str]:
    airport = airport.upper()
    if airport not in AIRPORTS:
        raise FlightInfoError("Choose one of the listed Swedavia airport codes.")
    if direction not in {"arrivals", "departures"}:
        raise FlightInfoError("Direction must be arrivals or departures.")
    # The API date path is yyyy-mm-dd and uses UTC.
    day = flight_date or datetime.now(timezone.utc).date()
    data, headers = api_request(f"{airport}/{direction}/{day:%Y-%m-%d}")
    return _flight_list(data), headers.get("last-modified", "")


def search_flights(
    flights: list[dict[str, Any]], query: str, direction: str
) -> list[dict[str, Any]]:
    """Match flights by the other airport's name or IATA code."""
    needle = re.sub(r"[^a-z0-9]", "", query.casefold())
    if not needle:
        return flights
    if direction == "arrivals":
        names = ("DepartureAirportEnglish", "DepartureAirportSwedish", "DepartureAirportIata")
    else:
        names = ("ArrivalAirportEnglish", "ArrivalAirportSwedish", "ArrivalAirportIata")
    matches = []
    for flight in flights:
        for name in names:
            value = _get(flight, name, default="")
            if value and needle in re.sub(r"[^a-z0-9]", "", str(value).casefold()):
                matches.append(flight)
                break
    return matches


def _text(mapping: Any, *names: str, default: str = "—") -> str:
    value = _get(mapping, *names, default="")
    if isinstance(value, (dict, list)) or value == "":
        return default
    return str(value)


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        # Use Windows' system timezone rules; no optional tzdata package needed.
        return parsed.astimezone()
    except ValueError:
        return None


def _fmt_time(value: Any) -> str:
    parsed = _parse_utc(value)
    return parsed.strftime("%H:%M") if parsed else "—"


def _local_zone_label(value: Any) -> str:
    parsed = _parse_utc(value)
    if not parsed:
        return "local"
    offset = parsed.utcoffset()
    if offset == timedelta(hours=1):
        return "CET"
    if offset == timedelta(hours=2):
        return "CEST"
    return parsed.tzname() or "local"


def _time_pair(value: Any) -> str:
    parsed = _parse_utc(value)
    if not parsed:
        return "—"
    utc_time = parsed.astimezone(timezone.utc).strftime("%H:%M")
    return f"{utc_time} UTC → {parsed:%H:%M} {_local_zone_label(value)}"


def _get_flight_time(flight: dict[str, Any], direction: str) -> dict[str, Any]:
    time_key = "ArrivalTime" if direction == "arrivals" else "DepartureTime"
    return _nested(flight, time_key)


def _is_upcoming(flight: dict[str, Any], direction: str) -> bool:
    times = _get_flight_time(flight, direction)
    if _get(times, "ActualUtc", default=""):
        return False
    scheduled = _parse_utc(_get(times, "ScheduledUtc", default=""))
    if not scheduled:
        return False
    return scheduled >= datetime.now().astimezone()


def _row_data(flight: dict[str, Any], direction: str) -> dict[str, str]:
    times = _get_flight_time(flight, direction)
    loc = _nested(flight, "LocationAndStatus")
    airline = _nested(flight, "AirlineOperator")
    baggage = _nested(flight, "Baggage")
    other_airport_keys = (
        ("DepartureAirportEnglish", "DepartureAirportSwedish", "DepartureAirportIata")
        if direction == "arrivals"
        else ("ArrivalAirportEnglish", "ArrivalAirportSwedish", "ArrivalAirportIata")
    )
    other = _text(flight, *other_airport_keys)
    code = _text(flight, "FlightId")
    carrier = _text(airline, "Name", default="")
    carrier_code = _text(airline, "IATA", default="")
    if carrier_code == "—":
        carrier_code = ""
    carrier_label = carrier or carrier_code or "—"
    if carrier and carrier_code:
        carrier_label = f"{carrier} ({carrier_code})"

    scheduled = _get(times, "ScheduledUtc", default="")
    estimated = _get(times, "EstimatedUtc", default="")
    actual = _get(times, "ActualUtc", default="")
    status = _text(loc, "FlightLegStatusEnglish", "FlightLegStatusSwedish", "FlightLegStatus", default="Scheduled")
    if status == "—":
        status = "Scheduled"
    status = STATUS_NAMES.get(status.upper(), status)
    return {
        "flight": code,
        "airline": carrier_label,
        "other": other,
        "scheduled": _time_pair(scheduled),
        "estimated": _time_pair(estimated),
        "status": status,
        "terminal": _text(loc, "Terminal"),
        "gate": _text(loc, "Gate"),
        "baggage": _text(baggage, "BaggageClaimUnit") if direction == "arrivals" else "—",
    }


def _print_flight(flight: dict[str, Any], index: int, direction: str) -> None:
    row = _row_data(flight, direction)
    arrow = "←" if direction == "arrivals" else "→"
    print(f"\n[{index}] {arrow}  {row['flight']}  |  {row['airline']}")
    place = "From" if direction == "arrivals" else "To"
    print(f"     {place}: {row['other']}")
    print(f"     Status: {row['status']}")
    print(f"     Terminal: {row['terminal']}  |  Gate: {row['gate']}")
    print(f"     Baggage: {row['baggage']}")
    print(f"     Sched: {row['scheduled']}")
    print(f"     Est:   {row['estimated']}")


def show_flights(airport: str, direction: str) -> None:
    print(f"\nLoading {direction} for {airport} ...")
    flights, last_modified = fetch_flights(airport, direction)
    flights = [flight for flight in flights if _is_upcoming(flight, direction)]
    flights.sort(key=lambda flight: _parse_utc(_get(_get_flight_time(flight, direction), "ScheduledUtc", default="")) or datetime.max.replace(tzinfo=timezone.utc))
    local_now = datetime.now().astimezone()
    zone_label = "CET" if local_now.utcoffset() == timedelta(hours=1) else "CEST" if local_now.utcoffset() == timedelta(hours=2) else local_now.tzname()
    print(f"\n{AIRPORTS[airport]} ({airport})  |  {direction.title()}  |  {local_now:%Y-%m-%d %H:%M} {zone_label}")
    print("─" * 72)
    if last_modified:
        print(f"Swedavia data updated: {last_modified}")
    if not flights:
        print("No flights were returned for today.")
        return

    total = len(flights)
    for start in range(0, total, PAGE_SIZE):
        page = flights[start : start + PAGE_SIZE]
        for offset, flight in enumerate(page, start + 1):
            _print_flight(flight, offset, direction)
        shown = min(start + len(page), total)
        if shown < total:
            answer = input(f"\n[{shown}/{total}] Enter = next {PAGE_SIZE}, m = menu, q = quit: ").strip().lower()
            if answer == "q":
                raise SystemExit(0)
            if answer == "m":
                return
            time.sleep(0.2)


def choose_airport(current: str) -> str:
    print("\nSwedavia airports")
    for code, name in AIRPORTS.items():
        print(f"  {code}  {name}")
    answer = input(f"Airport code [{current}]: ").strip().upper()
    if not answer:
        return current
    if answer not in AIRPORTS:
        print("Unknown airport code; keeping the current airport.")
        return current
    return answer


def main() -> None:
    print("SWEDAVIA FLIGHTINFO  /  CONSOLE BOARD")
    print("Live arrivals and departures for Swedish airports\n")
    airport = choose_airport("ARN")

    while True:
        print(f"\n{AIRPORTS[airport]} ({airport})")
        print("  1  Arrivals")
        print("  2  Departures")
        print("  3  Search flights by destination")
        print("  4  Change airport")
        print("  5  Refresh current airport")
        print("  6  Quit")
        choice = input("\nChoose [1-6]: ").strip().lower()
        try:
            if choice == "1":
                show_flights(airport, "arrivals")
            elif choice == "2":
                show_flights(airport, "departures")
            elif choice == "3":
                direction = input("Search arrivals or departures? [a/d]: ").strip().lower()
                direction = "arrivals" if direction.startswith("a") else "departures"
                query = input("Destination name or airport code: ").strip()
                flights, _ = fetch_flights(airport, direction)
                flights = [flight for flight in flights if _is_upcoming(flight, direction)]
                matches = search_flights(flights, query, direction)
                if matches:
                    for i, flight in enumerate(matches, 1):
                        _print_flight(flight, i, direction)
                else:
                    print("No matching flights found.")
            elif choice == "4":
                airport = choose_airport(airport)
            elif choice == "5":
                for direction in ("arrivals", "departures"):
                    show_flights(airport, direction)
            elif choice in {"6", "q", "quit"}:
                print("Goodbye.")
                return
            else:
                print("Choose a number from 1 to 6.")
        except FlightInfoError as exc:
            print(f"\nError: {exc}")
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            return


if __name__ == "__main__":
    main()
