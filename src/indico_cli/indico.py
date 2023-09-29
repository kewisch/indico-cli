import csv
import io
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

    def _request(self, *args, expect_code=200, ignore_code=False, **kwargs):
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

        if not ignore_code and r.status_code != expect_code:
            raise Exception(
                f"Request for {args[0]} {args[1]} failed with {r.status_code}: {r.text}"
            )

        return r

    def _request_json(self, *args, **kwargs):
        if "headers" not in kwargs:
            kwargs["headers"] = {}
        kwargs["headers"]["content-type"] = "application/json"

        if "data" in kwargs:
            kwargs["data"] = json.dumps(kwargs["data"])

        return self._request(*args, **kwargs)

    def adduser(self, email, firstname, lastname, affiliation):
        data = {
            "first_name": firstname,
            "last_name": lastname,
            "email": email,
            "affiliation": affiliation,
        }
        url = urljoin(self.urlbase, "/admin/users/create/")
        r = self._request("POST", url, data=data)

        return r.json()

    def searchgroup(self, text, exact=True):
        params = {"name": text, "exact": str(exact).lower()}
        url = urljoin(self.urlbase, "/groups/api/search")
        r = self._request("GET", url, params=params)
        return r.json()["groups"]

    def searchuser(self, email):
        params = {"email": email, "exact": "true"}
        url = urljoin(self.urlbase, "/user/search/")
        r = self._request("GET", url, params=params)
        return r.json()["users"]

    def getgroupusers(self, group):
        url = urljoin(self.urlbase, f"/admin/groups/indico/{group}/members")
        r = self._request("GET", url)

        data = r.json()
        doc = lxml.html.document_fromstring(data["html"])

        groups = [
            int(link.attrib["data-href"].split("/")[-1])
            for link in doc.xpath("//table/tbody/tr/td/a")
        ]
        return groups

    def editgroup(self, group, members):
        data = {"members": json.dumps(list(map(lambda u: "User:" + str(u), members)))}
        url = urljoin(self.urlbase, f"/admin/groups/indico/{group}/edit")
        r = self._request("POST", url, data=data, expect_code=302)

        if r.headers["location"] != "/admin/groups/":
            raise Exception("Unexpected response")

    def get_registrations(self, conference):
        url = urljoin(self.urlbase, f"/api/events/{conference}/registrants")
        r = self._request("GET", url)
        return r.json()["registrants"]

    def query_registration(self, conference, regform, query={}, fields=["email"]):
        url = urljoin(
            self.urlbase,
            f"/event/{conference}/manage/registration/{regform}/registrations/customize",
        )

        hasId = False
        if "id" in fields:
            hasId = True
            fields.remove("id")

        query["visible_items"] = json.dumps(fields)

        r = self._request("POST", url, data=query)
        rdata = r.json()
        doc = lxml.html.document_fromstring(rdata["html"])
        rows = doc.cssselect("table tbody tr")
        headers = [th.text_content() for th in doc.cssselect("table thead th")]
        results = []
        for row in rows:
            result = {}
            for idx, td in enumerate(row.cssselect("td")):
                if not headers[idx] or headers[idx] == "Full name":
                    continue
                elif headers[idx] == "ID":
                    if hasId:
                        result[headers[idx]] = int(td.text_content().strip()[1:])
                elif "data-text" in td.attrib:
                    result[headers[idx]] = td.attrib["data-text"]
                else:
                    result[headers[idx]] = td.text_content().strip()
            results.append(result)

        return results

    def regedit(self, conference, regform, regid, customfields={}, notify=False):
        data = customfields.copy()
        data["notify_user"] = notify

        url = urljoin(
            self.urlbase,
            f"/event/{conference}/manage/registration/{regform}/registrations/{regid}/edit",
        )
        r = self._request("POST", url, json=data)

        rdata = r.json()
        if not rdata["redirect"]:
            raise Exception("Unexpected response")

    def regfields(self, conference, regform):
        url = urljoin(
            self.urlbase,
            f"/event/{conference}/manage/registration/{regform}/form/",
        )
        r = self._request("GET", url)
        doc = lxml.html.document_fromstring(r.text)

        node = doc.cssselect("#registration-form-setup-container")[0]
        data = json.loads(node.attrib["data-form-data"])
        return data

    def regcsvimport(self, conference, regform, rowdata, moderate=False, notify=False):
        url = urljoin(
            self.urlbase,
            f"/event/{conference}/manage/registration/{regform}/registrations/import",
        )

        csvout = io.StringIO()
        writer = csv.writer(csvout)
        writer.writerows(rowdata)

        files = {"source_file": ("import.csv", csvout.getvalue())}
        data = {
            "__file_change_trigger": "added-file",
        }
        if not moderate:
            data["skip_moderation"] = "y"

        if notify:
            data["notify_users"] = "y"

        r = self._request("POST", url, ignore_code=True, files=files, data=data)

        if r.status_code == 400:
            doc = lxml.html.document_fromstring(r.text)
            error = doc.cssselect(".main .error-box p")[0].text_content().strip()
            raise Exception(error)

        if r.status_code != 200:
            print(r.text)
            raise Exception(f"Request failed with {r.status_code}")

    def get_contributions(self, conference, cache=False, tz=None):
        if cache:
            with open("contributions.json", "r") as gp:
                return json.load(gp)

        params = {"detail": "contributions"}
        if tz:
            params["tz"] = tz
        url = urljoin(self.urlbase, f"/export/event/{conference}.json")
        r = self._request("GET", url, params=params)
        return r.json()

    def get_contribution_entry(self, conference, cid):
        url = urljoin(self.urlbase, f"/event/{conference}/contributions/{cid}.json")
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
                            f"/event/{conference}/contributions/{centry['db_id']}",
                        ),
                    )
                    break

    def get_contribution_edit_entry(self, conference, cid):
        url = urljoin(
            self.urlbase, f"/event/{conference}/manage/contributions/{cid}/edit"
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

        url = urljoin(self.urlbase, f"/export/timetable/{conference}.json")
        r = self._request("GET", url)
        return r.json()

    def check_author_overlap(self, conference):
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

            # print(author,"\n\t" + "\n\t".join(map(lambda e: e[0] + " – " + e[1], sentries)))
            # print(author)

            lastentry = sentries[0]
            for entry in sentries[1:]:
                if time_int2(entry[0]) < time_int2(lastentry[1]):
                    print(
                        f"\tConflict: {entry[2]} @{entry[0]} - {entry[1]} vs {lastentry[2]} @{lastentry[0]} - {lastentry[1]}"
                    )
                lastentry = entry

        for room, entries in byroom.items():
            sentries = sorted(entries, key=lambda e: time_int2(e[0]))
            lastentry = sentries[0]
            for entry in sentries[1:]:
                if time_int2(entry[0]) < time_int2(lastentry[1]):
                    print(
                        f"\tTime/Room Conflict in {room}: {entry[2]} @{entry[0]} - {entry[1]} vs {lastentry[2]} @{lastentry[0]} - {lastentry[1]}"
                    )
                lastentry = entry

    def convert_timetable_to_contrib(self, conference, cid):
        url = urljoin(
            self.urlbase, f"/event/{conference}/manage/timetable/entry/{cid}/info"
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
            self.urlbase, f"/event/{conference}/manage/timetable/entry/{cid}/edit/"
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
            self.urlbase, f"/event/{conference}/manage/timetable/entry/{cid}/edit/"
        )

        entry["person_link_data"] = json.dumps(entry["person_link_data"])
        entry["location_data"] = json.dumps(entry["location_data"])
        entry["references"] = json.dumps(entry["references"])

        r = self._request("POST", url, data=entry)
        return r.json()

    def move_timetable(self, conference, tid, day, parent=None):
        url = urljoin(
            self.urlbase, f"/event/{conference}/manage/timetable/entry/{tid}/move"
        )
        data = {"day": day}

        if parent:
            data["parent_id"] = parent

        r = self._request_json("POST", url, data=data)
        return r.json()

    def change_time(self, conference, contribution, startDate, endDate):
        url = urljoin(
            self.urlbase,
            f"/event/{conference}/manage/timetable/entry/{contribution}/edit/datetime",
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
            durA = int(editEntryA["duration"]) / 60
            durB = int(editEntryB["duration"]) / 60
            print(f"Error: Mismatch in duration ({durA} vs {durB})")
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
            url = urljoin(self.urlbase, f"/event/{conference}/manage/logs/api/logs")
            r = self._request("GET", url, params=params)
            data = r.json()
            print(f"page {current_page} total {data['pages'][-1]}", file=sys.stderr)

            entries.extend(data["entries"])
            for entry in data["entries"]:
                recipients.extend(entry["payload"]["to"])

            if current_page == data["pages"][-1]:
                break

            current_page += 1

        return recipients

    def contributions_link(self, conference, contribId, link, title):
        url = urljoin(
            self.urlbase,
            "/event/{conference}/manage/contributions/{contribId}/attachments/add/link",
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
