import http.client
import logging
from collections.abc import Mapping
from datetime import datetime

from dateutil.parser import parse as dateparse

CSV_DEFAULT_FIELDS = ("first_name", "last_name", "affiliation", "position", "phone")


class IndicoCliException(Exception):
    pass


def init_logging(level, _):
    logging.basicConfig()
    logging.getLogger().setLevel(level)
    requests_log = logging.getLogger("requests.packages.urllib3")
    requests_log.setLevel(level)
    requests_log.propagate = True

    if level == logging.DEBUG:
        http.client.HTTPConnection.debuglevel = 1


def parsedate(fieldvalue, autodate=False, dateonly=False):
    if fieldvalue == "":
        date = None
    elif autodate:
        date = dateparse(fieldvalue)
    else:
        try:
            date = datetime.fromisoformat(fieldvalue)
        except ValueError:
            raise IndicoCliException(
                "Invalid date format '{}', use --autodate or correct the CSV file to use ISO8601 yyyy-mm-ddThh:mm:ss".format(
                    fieldvalue
                )
            )

    if date is None:
        return ""
    elif dateonly:
        return date.strftime("%Y-%m-%d")
    else:
        return date.isoformat(timespec="seconds")


def create_register_fields(row, rawfieldmap, rawfields=False):
    def lookupfield(name):
        return rawfieldmap.get(name, {}).get("htmlName" if rawfields else "title", None)

    return [
        row[lookupfield("first_name")],
        row[lookupfield("last_name")],
        row.get(lookupfield("affiliation"), ""),
        row.get(lookupfield("position"), ""),
        row.get(lookupfield("phone"), ""),
        row[lookupfield("email")],
    ]


def setfield(data, fieldvalue, fielddata, autodate=False, allow_email=False):
    fieldname = fielddata["htmlName"]
    fieldtype = fielddata["inputType"]

    if fieldtype in ("checkbox", "bool"):
        data[fieldname] = True if fieldvalue.lower() in ("yes", "true", "1") else False
    elif fieldtype in ("single_choice", "multi_choice"):
        set_choice_field(data, fieldvalue, fielddata, autodate)
    elif fieldtype == "accommodation":
        set_accommodation_field(data, fieldvalue, fielddata, autodate)
    elif fieldtype == "country":
        set_country_field(data, fieldvalue, fielddata, autodate)
    elif fieldtype in ("textarea", "text", "phone", "number"):
        data[fieldname] = fieldvalue
    elif fieldtype == "email":
        if allow_email:
            data[fieldname] = fieldvalue
    elif fieldtype == "date":
        data[fieldname] = parsedate(fieldvalue, autodate)
    else:
        raise IndicoCliException("Unhandled field type: " + fieldtype)

    return data


def set_accommodation_field(data, fieldvalue, fielddata, autodate=False):
    fieldname = fielddata["htmlName"]
    fieldtype = fielddata["inputType"]

    if fieldtype != "accommodation":
        raise Exception("Wrong field type")

    if not len(fieldvalue):
        return

    data[fieldname] = datavalue = {}

    if fieldvalue == "none":
        choicedata = next(
            (choice for choice in fielddata["choices"] if choice["isNoAccommodation"]),
            None,
        )
    else:
        fromdate, todate, choice = fieldvalue.split(",", 2)
        try:
            pass
        except ValueError:
            raise IndicoCliException(
                f"Format of accommodation field is e.g. '2021-01-01,2021-01-02,Option Name', got '{fieldvalue}'"
            )

        if choice not in fielddata["_rev_captions"]:
            raise IndicoCliException(
                "Couldn't find choice '{}' for field '{}'".format(
                    choice, fielddata["title"]
                )
            )
        choicedata = fielddata["_choicemap"][fielddata["_rev_captions"][choice]]
        if not choicedata["isNoAccommodation"]:
            datavalue["arrivalDate"] = parsedate(fromdate, autodate, True)
            datavalue["departureDate"] = parsedate(todate, autodate, True)

            if datavalue["departureDate"] <= datavalue["arrivalDate"]:
                raise IndicoCliException(
                    f"Departure date {datavalue['departureDate']} is before or equal to arrival date {datavalue['arrivalDate']}"
                )
            if datavalue["arrivalDate"] < fielddata["arrivalDateFrom"]:
                raise IndicoCliException(
                    f"Date {datavalue['arrivalDate']} is before allowed arrival date {fielddata['arrivalDateFrom']}"
                )
            if datavalue["departureDate"] > fielddata["departureDateTo"]:
                raise IndicoCliException(
                    f"Date {datavalue['departureDate']} is after allowed departure date {fielddata['departureDateTo']}"
                )

    datavalue["isNoAccommodation"] = choicedata["isNoAccommodation"]
    datavalue["choice"] = choicedata["id"]


def set_choice_field(data, fieldvalue, fielddata, autodate=False):
    fieldname = fielddata["htmlName"]
    fieldtype = fielddata["inputType"]

    if fieldtype not in ("single_choice", "multi_choice"):
        raise Exception("Wrong field type")

    data[fieldname] = datavalue = {}
    choices = fieldvalue.split(",") if fieldtype == "multi_choice" else [fieldvalue]

    for choice in choices:
        if len(choice):
            if choice not in fielddata["_rev_captions"]:
                raise IndicoCliException(
                    f"Couldn't find choice '{choice}' for field '{fielddata['title']}'"
                )
            choiceid = fielddata["_rev_captions"][choice]
            datavalue[choiceid] = 1


def set_country_field(data, fieldvalue, fielddata, autodate=False):
    fieldname = fielddata["htmlName"]
    fieldtype = fielddata["inputType"]

    if fieldtype != "country":
        raise Exception("Wrong field type")

    if fieldvalue == "":
        data[fieldname] = ""
    else:
        found = False
        for val in fielddata["choices"]:
            if val["caption"] == fieldvalue:
                data[fieldname] = val["countryKey"]
                found = True
                break

        if not found:
            raise IndicoCliException("Could not find country " + fieldvalue)


def fieldnamemap(fieldinfo, rawfields):
    data = {}
    rawdata = {}
    for field, fielddata in fieldinfo["items"].items():
        section = fieldinfo["sections"][str(fielddata["sectionId"])]
        if (
            not section["enabled"]
            or not fielddata["isEnabled"]
            # Readonly fields like labels don't have a htmlName
            or "htmlName" not in fielddata
        ):
            continue

        if fielddata["title"] in data:
            raise IndicoCliException(
                "Ambiguous field info, use raw field names instead: "
                + fielddata["title"]
            )
        data[fielddata["title"]] = fielddata
        rawdata[fielddata["htmlName"]] = fielddata
        if "captions" in fielddata:
            fielddata["_rev_captions"] = {
                v: k for k, v in fielddata["captions"].items()
            }

        try:
            fielddata["_choicemap"] = {
                choice["id"]: choice for choice in fielddata["choices"]
            }
        except KeyError:
            # Ok to skip if there is no id key
            pass

    if rawfields:
        return rawdata, rawdata
    else:
        return data, rawdata


class RegIdMap(Mapping):
    def __init__(self, indico, conference, noisy=True):
        super().__init__()
        self._indico = indico
        self._conference = conference
        self._noisy = noisy
        self._cache = None

    def _ensurecache(self):
        if not self._cache:
            if self._noisy:
                print("Looking up emails...", end="", flush=True)
            self._cache = dict(
                map(
                    lambda row: (row["personal_data"]["email"], row["registrant_id"]),
                    self._indico.get_registrations(self._conference),
                )
            )
            if self._noisy:
                print("Done")

    def __getitem__(self, key):
        self._ensurecache()
        return self._cache[key]

    def __iter__(self):
        self._ensurecache()
        return self._cache.__iter__()

    def __len__(self):
        self._ensurecache()
        return self._cache.__len__()
