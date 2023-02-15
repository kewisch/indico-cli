import http.client
import logging
from datetime import datetime

from dateutil.parser import parse as dateparse


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


def setfield(data, fieldvalue, fielddata, autodate=False):
    fieldname = fielddata["htmlName"]
    fieldtype = fielddata["inputType"]

    if fieldtype in ("checkbox", "bool"):
        data[fieldname] = True if fieldvalue.lower() in ("yes", "true", "1") else False
    elif fieldtype in ("single_choice", "multi_choice", "accommodation"):

        choices = fieldvalue.split(",") if fieldtype == "multi_choice" else [fieldvalue]
        datavalue = {}

        for choice in choices:
            if len(choice):
                if choice not in fielddata["rev_captions"]:
                    raise IndicoCliException(
                        "Couldn't find choice {} for field '{}'".format(
                            choice, fielddata["title"]
                        )
                    )
                datavalue[fielddata["rev_captions"][choice]] = 1

        data[fieldname] = datavalue
    elif fieldtype == "country":
        found = False
        for val in fielddata["choices"]:
            if val["caption"] == fieldvalue:
                data[fieldname] = val["countryKey"]
                found = True
                break

        if not found:
            raise IndicoCliException("Could not find country " + fieldvalue)

    elif fieldtype in ("textarea", "text", "phone", "number"):
        data[fieldname] = fieldvalue
    elif fieldtype == "email":
        pass  # This is the key, never set it
    elif fieldtype == "date":
        if autodate:
            data[fieldname] = dateparse(fieldvalue).isoformat(timespec="seconds")
        else:
            try:
                data[fieldname] = datetime.fromisoformat(fieldvalue).isoformat(
                    timespec="seconds"
                )
            except ValueError:
                raise IndicoCliException(
                    "Invalid date format '{}', use --autodate or correct the CSV file to use ISO8601 yyyy-mm-ddThh:mm:ss".format(
                        fieldvalue
                    )
                )
    else:
        raise IndicoCliException("Unhandled field type: " + fieldtype)

    return data


def fieldnamemap(fieldinfo, rawfields):
    data = {}
    rawdata = {}
    for field, fielddata in fieldinfo.items():
        if fielddata["title"] in data:
            raise IndicoCliException(
                "Ambiguous field info, use raw field names instead"
            )
        data[fielddata["title"]] = fielddata
        rawdata[fielddata["htmlName"]] = fielddata
        if "captions" in fielddata:
            fielddata["rev_captions"] = {v: k for k, v in fielddata["captions"].items()}

    if rawfields:
        return rawdata, rawdata
    else:
        return data, rawdata


def regidmap(indico, conference):
    return dict(
        map(
            lambda row: (row["personal_data"]["email"], row["registrant_id"]),
            indico.get_registrations(conference),
        )
    )
