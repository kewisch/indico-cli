import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from urllib.parse import urljoin

import lxml.html
import requests


def time_int(entry):
    dt = datetime.strptime(entry["date"] + "T" + entry["time"], "%Y-%m-%dT%H:%M:%S")
    return int((dt - datetime(1970, 1, 1)).total_seconds())


def time_int2(entry):
    dt = datetime.strptime(entry, "%Y-%m-%dT%H:%M:%S")
    return int((dt - datetime(1970, 1, 1)).total_seconds())


class Indico:
    def __init__(self, urlbase, token):
        self.headers = {"Authorization": "Bearer " + token}
        self.urlbase = urlbase

    def _request(self, *args, **kwargs):

        if "headers" not in kwargs:
            kwargs["headers"] = {}
        kwargs["headers"].update(self.headers)
        kwargs["allow_redirects"] = False
        kwargs["headers"]["accept"] = "application/json"
        kwargs["headers"]["cache-control"] = "no-cache"

        r = requests.request(*args, **kwargs)
        if "location" in r.headers and "/login/" in r.headers["location"]:
            print("Your token has expired")
            sys.exit(1)

        if r.status_code != 200:
            raise Exception(
                "Request for {} {} failed with {}".format(
                    args[0], args[1], r.status_code
                )
            )

        return r

    def _request_json(self, *args, **kwargs):
        if "headers" not in kwargs:
            kwargs["headers"] = {}
        kwargs["headers"]["content-type"] = "application/json"

        if "data" in kwargs:
            kwargs["data"] = json.dumps(kwargs["data"])

        return self._request(*args, **kwargs)

    def get_contributions(self, conference):
        # with open("contributions.json", "r") as gp:
        #    return json.load(gp)
        params = {"detail": "contributions"}
        url = urljoin(self.urlbase, "/export/event/{}.json".format(conference))
        r = self._request("GET", url, params=params)
        return r.json()

    def get_contribution_entry(self, conference, cid):
        url = urljoin(
            self.urlbase, "/event/{}/contributions/{}.json".format(conference, cid)
        )
        r = self._request("GET", url)
        return r.json()

    def check_contrib_submitter(self, conference):
        """
        Problem: Some contribution authors don't have the "submitter" bit set, so they can't make
                 changes or upload slides.
        Fix: Run this command, then manually click on each URL and click the paperclip icon
        """
        data = self.get_contributions(conference)

        for centry in data["results"][0]["contributions"]:
            if not centry["startDate"]:
                continue
            if (
                len(centry["speakers"])
                + len(centry["primaryauthors"])
                + len(centry["coauthors"])
                == 0
            ):
                continue
            try:
                editentry = self.get_contribution_edit_entry(
                    conference, centry["db_id"]
                )
            except Exception:
                print(centry)
                continue
            for person in editentry["person_link_data"]:
                if not ("submitter" in person["roles"]):
                    print(
                        person["name"],
                        urljoin(
                            self.urlbase,
                            "/event/{}/contributions/{}".format(
                                conference, centry["db_id"]
                            ),
                        ),
                    )
                    break

    def get_contribution_edit_entry(self, conference, cid):
        url = urljoin(
            self.urlbase,
            "/event/{}/manage/contributions/{}/edit".format(conference, cid),
        )
        params = {"standalone": 1, "_": int(time.time())}
        r = self._request("GET", url, params=params)
        data = r.json()

        doc = lxml.html.document_fromstring(data["html"])

        entry = dict(doc.forms[0].form_values())
        entry["person_link_data"] = json.loads(entry["person_link_data"])
        entry["location_data"] = json.loads(entry["location_data"])
        entry["references"] = json.loads(entry["references"])

        return entry

    def get_timetable(self, conference):
        # with open("timetable.json", "r") as gp:
        #    return json.load(gp)

        url = urljoin(self.urlbase, "/export/timetable/{}.json".format(conference))
        r = self._request("GET", url)
        return r.json()

    def check_overlap(self, conference):
        table = self.get_contributions(conference)
        byauthor = defaultdict(set)
        byroom = defaultdict(set)

        for entry in table["results"][0]["contributions"]:
            if not entry["startDate"] or not entry["endDate"]:
                print("Unscheduled:", entry["title"])
                continue

            for presenter in entry["speakers"]:
                if entry["startDate"] and entry["endDate"]:
                    byauthor[presenter["email"]].add(
                        (
                            entry["startDate"]["date"]
                            + "T"
                            + entry["startDate"]["time"],
                            entry["endDate"]["date"] + "T" + entry["endDate"]["time"],
                            entry["title"],
                        )
                    )
            for presenter in entry["primaryauthors"]:
                if entry["startDate"] and entry["endDate"]:
                    byauthor[presenter["email"]].add(
                        (
                            entry["startDate"]["date"]
                            + "T"
                            + entry["startDate"]["time"],
                            entry["endDate"]["date"] + "T" + entry["endDate"]["time"],
                            entry["title"],
                        )
                    )
            for presenter in entry["coauthors"]:
                if entry["startDate"] and entry["endDate"]:
                    byauthor[presenter["email"]].add(
                        (
                            entry["startDate"]["date"]
                            + "T"
                            + entry["startDate"]["time"],
                            entry["endDate"]["date"] + "T" + entry["endDate"]["time"],
                            entry["title"],
                        )
                    )

            byroom[entry["roomFullname"]].add(
                (
                    entry["startDate"]["date"] + "T" + entry["startDate"]["time"],
                    entry["endDate"]["date"] + "T" + entry["endDate"]["time"],
                    entry["title"],
                )
            )

        for author, entries in byauthor.items():
            if len(entries) == 1:
                continue

            sentries = sorted(entries, key=lambda e: time_int2(e[0]))

            # print(author,"\n\t" + "\n\t".join(map(lambda e: e[0] + " ??? " + e[1], sentries)))
            print(author)

            lastentry = sentries[0]
            for entry in sentries[1:]:
                if time_int2(entry[0]) < time_int2(lastentry[1]):
                    print(
                        "\tConflict:",
                        entry[2]
                        + " @"
                        + entry[0]
                        + " - "
                        + entry[1]
                        + " vs "
                        + lastentry[2]
                        + " @"
                        + lastentry[0]
                        + " - "
                        + lastentry[1],
                    )
                lastentry = entry

        for room, entries in byroom.items():
            sentries = sorted(entries, key=lambda e: time_int2(e[0]))
            lastentry = sentries[0]
            for entry in sentries[1:]:
                if time_int2(entry[0]) < time_int2(lastentry[1]):
                    print(
                        "\tTime/Room Conflict in ",
                        room,
                        ":",
                        entry[2]
                        + " @"
                        + entry[0]
                        + " - "
                        + entry[1]
                        + " vs "
                        + lastentry[2]
                        + " @"
                        + lastentry[0]
                        + " - "
                        + lastentry[1],
                    )
                lastentry = entry

    def convert_timetable_to_contrib(self, conference, cid):
        url = urljoin(
            self.urlbase,
            "/event/{}/manage/timetable/entry/{}/info".format(conference, cid),
        )
        r = self._request("GET", url)

        data = r.json()
        doc = lxml.html.document_fromstring(data["html"])
        href = doc.cssselect(".description")[0].attrib["data-display-href"]
        return int(href.split("/")[-2])

    def get_all_timetable_entries(self, conference, byKey="id"):
        entries = {}
        table = self.get_timetable(conference)
        for day in table["results"][str(conference)].values():
            for entry in day.values():
                if entry["entryType"] == "Contribution":
                    entries[int(entry[byKey])] = entry
                if entry["entryType"] == "Session":
                    for subentry in entry["entries"].values():
                        if subentry["entryType"] == "Contribution":
                            entries[int(subentry[byKey])] = subentry
        return entries

    def get_timetable_edit_entry(self, conference, cid):

        url = urljoin(
            self.urlbase,
            "/event/{}/manage/timetable/entry/{}/edit/".format(conference, cid),
        )
        r = self._request("GET", url)

        data = r.json()
        doc = lxml.html.document_fromstring(data["html"])

        entry = dict(doc.forms[0].form_values())
        entry["person_link_data"] = json.loads(entry["person_link_data"])
        entry["location_data"] = json.loads(entry["location_data"])
        entry["references"] = json.loads(entry["references"])

        return entry

    def edit_timetable_entry(self, conference, cid, entry):
        url = urljoin(
            self.urlbase,
            "/event/{}/manage/timetable/entry/{}/edit/".format(conference, cid),
        )

        entry["person_link_data"] = json.dumps(entry["person_link_data"])
        entry["location_data"] = json.dumps(entry["location_data"])
        entry["references"] = json.dumps(entry["references"])

        r = self._request("POST", url, data=entry)
        return r.json()

    def move_timetable(self, conference, tid, day, parent=None):
        url = urljoin(
            self.urlbase,
            "/event/{}/manage/timetable/entry/{}/move".format(conference, tid),
        )
        data = {"day": day}

        if parent:
            data["parent_id"] = parent

        r = self._request_json("POST", url, data=data)
        return r.json()

    def change_time(self, conference, contribution, startDate, endDate):
        url = urljoin(
            self.urlbase,
            "/event/{}/manage/timetable/entry/{}/edit/datetime".format(
                conference, contribution
            ),
        )
        data = {"startDate": startDate, "endDate": endDate}

        r = self._request("GET", url, params=data)
        data = r.json()

    def swap_timetable(self, conference, entryAID, entryBID, keyId="friendlyId"):
        table = self.get_all_timetable_entries(conference, keyId)
        if entryAID not in table:
            print("Missing ", entryAID)
            return
        if entryBID not in table:
            print("Missing ", entryBID)
            return

        entryA = table[entryAID]
        entryB = table[entryBID]
        timetableIDA = entryA["id"][1:]
        timetableIDB = entryB["id"][1:]

        editEntryA = self.get_timetable_edit_entry(conference, timetableIDA)
        editEntryB = self.get_timetable_edit_entry(conference, timetableIDB)

        if editEntryA["duration"] != editEntryB["duration"]:
            print(
                "Error: Mismatch in duration ({} vs {})".format(
                    int(editEntryA["duration"]) / 60, int(editEntryB["duration"]) / 60
                )
            )
            return

        editEntryA["location_data"], editEntryB["location_data"] = (
            editEntryB["location_data"],
            editEntryA["location_data"],
        )
        editEntryA["time"], editEntryB["time"] = editEntryB["time"], editEntryA["time"]

        self.edit_timetable_entry(conference, timetableIDA, editEntryA)
        self.edit_timetable_entry(conference, timetableIDB, editEntryB)

        if entryA["startDate"]["date"] != entryB["startDate"]["date"]:
            self.move_timetable(conference, timetableIDA, entryB["startDate"]["date"])
            self.move_timetable(conference, timetableIDB, entryA["startDate"]["date"])

    def get_log(
        self,
        conference,
        query,
        logtype=["emails", "event", "management", "participants", "reviewing"],
    ):
        params = {"filters": logtype, "page": 1, "q": query}
        entries = []
        recipients = []
        current_page = 1

        while True:
            params["page"] = current_page
            url = urljoin(
                self.urlbase, "/event/{}/manage/logs/api/logs".format(conference)
            )
            r = self._request("GET", url, params=params)
            data = r.json()
            print(
                "page {} total {}".format(current_page, data["pages"][-1]),
                file=sys.stderr,
            )

            entries.extend(data["entries"])
            for entry in data["entries"]:
                recipients.extend(entry["payload"]["to"])

            if current_page == data["pages"][-1]:
                break

            current_page += 1
            print(recipients)

        return recipients

    def contributions_link(self, conference, contribId, link, title):
        url = urljoin(
            self.urlbase,
            "/event/{}/manage/contributions/{}/attachments/add/link".format(
                conference, contribId
            ),
        )
        params = {"link_url": link, "title": title, "folder": "__None", "acl": "[]"}
        r = self._request_json("POST", url, data=params)
        data = r.json()

        doc = lxml.html.document_fromstring(data["flashed_messages"])
        message = doc.text_content().strip()
        if data["success"]:
            return "Success: " + message
        else:
            return "Fail: " + message
