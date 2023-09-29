import csv
import getpass
import json
import logging
import sys
from collections import defaultdict

import click
import keyring
import yaml
from tqdm import tqdm

from .indico import Indico
from .util import (
    CSV_DEFAULT_FIELDS,
    IndicoCliException,
    RegIdMap,
    create_register_fields,
    fieldnamemap,
    init_logging,
    setfield,
)

INDICO_ENVIRONMENTS = {
    "prod": "https://events.canonical.com",
    "stage": "https://events.staging.canonical.com",  # staging
    "local": "http://localhost:8000",  # local
}


def init_indico(env):
    token = keyring.get_password("indico", "token." + env)
    if token is None:
        token = getpass.getpass(f"Enter token for {env}: ")
        keyring.set_password("indico", "token." + env, token)

    if env not in INDICO_ENVIRONMENTS:
        raise click.UsageError(f"Invalid environment {env}")

    return Indico(INDICO_ENVIRONMENTS[env], token)


@click.group()
@click.option("--debug", is_flag=True, help="Enable debugging.")
@click.option(
    "--env",
    default="prod",
    type=click.Choice(("prod", "stage", "local")),
    help="Indico environment to use.",
)
@click.option("--config", default="~/.canonicalrc", help="Config file location.")
@click.pass_context
def main(ctx, debug, env, config):
    ctx.obj = init_indico(env)

    if debug:
        init_logging(logging.DEBUG)


@main.command()
@click.argument("email", required=True)
@click.argument("firstname", required=True)
@click.argument("familyname", required=True)
@click.argument("affiliation", required=False)
@click.pass_obj
def adduser(indico, email, firstname, familyname, affiliation):
    """Provision a user"""

    indico.adduser(email, firstname, familyname, affiliation)


@main.command()
@click.argument("group", required=True)
@click.argument("users", nargs=-1)
@click.pass_obj
def groupadduser(indico, group, users):
    """Adds a user to a group.

    Pass the id or name of the group, and the user(s) to add.
    """

    if group.isdigit():
        groupid = int(group)
    else:
        groupdata = indico.searchgroup(group)
        if len(groupdata) == 1:
            groupid = groupdata[0]["id"]
        else:
            raise IndicoCliException("Could not find group " + group)

    userids = set()
    for user in users:
        if user.isdigit():
            userids.add(int(user))
        else:
            userdata = indico.searchuser(user)
            if len(userdata) == 0:
                click.echo("Warning: Could not find user " + user)
            else:
                userids.add(userdata[0]["id"])

    existing_users = set(indico.getgroupusers(groupid))
    if userids.issubset(existing_users):
        click.echo(f"All users already in group {group}")
    else:
        userids.update(existing_users)
        indico.editgroup(groupid, list(userids))


@main.command()
@click.argument("conference", type=int)
@click.argument("regform", type=int)
@click.option(
    "--query",
    "-q",
    nargs=2,
    multiple=True,
    metavar="FIELDNAME VALUE",
    help="Query for a certain field value",
)
@click.option(
    "--fields", "-f", default="Email Address", help="Comma separated list of fields"
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(("csv", "json")),
    default="csv",
    help="Format to output in",
)
@click.pass_obj
def regquery(indico, conference, regform, query, fields, fmt):
    """Query registrations by filter

    Pass in the ids of the conference and regform, you can find them in the URL.

    """

    fieldinfo = indico.regfields(conference, regform)
    fieldmap, rawfieldmap = fieldnamemap(fieldinfo, False)

    try:
        fields = list(
            map(
                lambda field: fieldmap[field]["id"] if field != "ID" else "id",
                fields.split(","),
            )
        )
        querydict = {fieldmap[key]["htmlName"]: value for key, value in query}
    except KeyError as e:
        click.echo(f"Unknown field name: {e.args[0]}")
        sys.exit(1)

    data = indico.query_registration(
        conference, regform, query=querydict, fields=fields
    )

    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
    elif fmt == "csv":
        showheader = len(fields) > 1
        for row in data:
            header = list(row.keys())
            if showheader:
                click.echo(",".join(header))
                showheader = False

            click.echo(",".join(row.values()))


@main.command()
@click.argument("conference", type=int)
@click.argument("regform", type=int)
@click.argument("regids", nargs=-1)
@click.option(
    "--set",
    "-s",
    "setfields",
    nargs=2,
    multiple=True,
    metavar="FIELDNAME VALUE",
    help="Set a field",
)
@click.option("--allow-email", is_flag=True, help="Allow changing the Email field")
@click.option(
    "--rawfields",
    is_flag=True,
    help="Assume the CSV is using raw field names",
)
@click.option("--rawfileds", is_flag=True, help="Assume raw field names instead")
@click.option("--autodate", is_flag=True, help="Automatically parse date formats")
@click.option("--notify", is_flag=True, help="Notify the user of the change")
@click.option(
    "--all",
    "-a",
    "allreg",
    is_flag=True,
    help="Set the value on all registrants. CAVEAT: this only works with a single registration form",
)
@click.pass_obj
def regedit(
    indico,
    conference,
    regform,
    regids,
    setfields,
    allow_email,
    rawfields,
    autodate,
    notify,
    allreg,
):
    """Edit a user registration"""

    if allreg:
        click.echo("Retrieving all registration ids...", nl=False)
        regids = list(
            map(
                lambda row: row["registrant_id"],
                indico.get_registrations(conference),
            )
        )
        click.echo("Done")
    else:
        cachereg = RegIdMap(indico, conference)
        try:
            regids = list(
                map(
                    lambda regid: int(regid) if regid.isdigit() else cachereg[regid],
                    regids,
                )
            )
        except KeyError as e:
            raise click.BadParameter(f"{e.args[0]} not found")

    fieldinfo = indico.regfields(conference, regform)
    fieldmap, rawfieldmap = fieldnamemap(fieldinfo, rawfields=rawfields)
    for regid in tqdm(regids, desc="Setting fields", unit="users"):
        data = {}

        try:
            for key, value in setfields:
                setfield(
                    data,
                    value,
                    fieldmap[key],
                    autodate=autodate,
                    allow_email=allow_email,
                )

            indico.regedit(conference, regform, regid, data, notify)
        except IndicoCliException as e:
            tqdm.write(f"{regid} FAILED: {e}")
        except Exception as e:
            tqdm.write(f"{regid} FAILED: {type(e).__name__}: {e}")
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                raise e


@main.command()
@click.option("--disabled", "disabledfields", is_flag=True, help="Show disabled fields")
@click.argument("conference", type=int)
@click.argument("regform", type=int)
@click.pass_obj
def regfields(indico, disabledfields, conference, regform):
    """Get field names for CSV import"""

    fieldinfo = indico.regfields(conference, regform)
    click.echo(
        "When putting together the CSV file for import, use the Name of the field as"
    )
    click.echo(
        "the column header. You will only need the ID if you are using --rawfields\n"
    )
    for field, data in fieldinfo["items"].items():
        section = fieldinfo["sections"][str(data["sectionId"])]
        if "items" not in section:
            section["items"] = []

        section["items"].append(data)

    for sectionId, section in sorted(
        fieldinfo["sections"].items(), key=lambda sec: sec[1]["position"]
    ):
        if not disabledfields and not section["enabled"]:
            continue
        click.echo(
            f"    Section: {section['title']}"
            + (" (disabled)" if not section["enabled"] else "")
        )
        for data in sorted(section["items"], key=lambda itm: itm["position"]):
            if not disabledfields and not data["isEnabled"]:
                continue

            if data["inputType"] == "label":
                click.echo(
                    f"        ID: field_{data['id']:<4}   Type: {data['inputType']:<20} Name: {data['title']} (readonly)"
                    + (" (disabled)" if not data["isEnabled"] else "")
                )
            elif "htmlName" in data:
                click.echo(
                    f"        ID: {data['htmlName']:<10}   Type: {data['inputType']:<20} Name: {data['title']}"
                    + (" (disabled)" if not data["isEnabled"] else "")
                )
            else:
                click.echo("        Unhandled field type: " + str(data))

            if "captions" in data:
                click.echo("            Choices:")
                for uid, caption in data["captions"].items():
                    click.echo(f"                {caption} ({uid})")
                click.echo("\n")


@main.command()
@click.argument("conference", type=int)
@click.argument("regform", type=int)
@click.argument("csvfile", type=click.File("r"))
@click.option("--register", is_flag=True, help="Register users if they don't exist")
@click.option("--autodate", is_flag=True, help="Automatically parse date formats")
@click.option(
    "--rawfields",
    is_flag=True,
    help="Assume the CSV is using raw field names",
)
@click.option("--notify", is_flag=True, help="Notify the user of the change")
@click.pass_obj
def regeditcsv(
    indico, conference, regform, csvfile, register, autodate, rawfields, notify
):
    """Bulk edit user registration via csv"""

    click.echo("Loading field and registration data...", nl=False)
    fieldinfo = indico.regfields(conference, regform)
    fieldmap, rawfieldmap = fieldnamemap(fieldinfo, rawfields)
    cachereg = RegIdMap(indico, conference, noisy=False)
    click.echo("Done")

    fieldnames = None
    rows = None
    registerusers = {}

    def lookupfield(name):
        return rawfieldmap.get(name, {}).get("htmlName" if rawfields else "title", None)

    emailfield = lookupfield("email")

    reader = csv.DictReader(csvfile)
    if emailfield not in reader.fieldnames:
        raise IndicoCliException("Missing 'Email Address' field in csv file")

    fieldnames = reader.fieldnames
    rows = list(reader)

    if register:
        for row in rows:
            if row[emailfield] not in cachereg:
                try:
                    registerusers[row[emailfield]] = create_register_fields(
                        row, rawfieldmap, rawfields
                    )
                except KeyError as e:
                    raise IndicoCliException(
                        f"User {row[emailfield]} is not previously registered, CSV requires at "
                        + "least email, firstname and lastname fields, optionally also "
                        + f"affiliation, position (team) (Missing: {e.args[0]})"
                    )

        if len(registerusers) > 0:
            click.echo(f"Registering {len(registerusers)} new users...", nl=False)
            indico.regcsvimport(
                conference,
                regform,
                registerusers.values(),
                notify=notify,
            )
            click.echo("Done")
            # Reload cache to get new reg ids
            cachereg = RegIdMap(indico, conference)

    for row in tqdm(rows, desc="Setting fields", unit="users"):
        try:
            if row[emailfield] not in cachereg:
                raise IndicoCliException(
                    "User is not registered, use --register if needed"
                )
            regid = cachereg[row[emailfield]]

            data = {}
            for field in fieldnames:
                if field == emailfield:
                    continue
                if field not in fieldmap:
                    raise IndicoCliException(
                        "Could not find registration field: " + field
                    )
                if (
                    row[emailfield] in registerusers
                    and fieldmap[field]["htmlName"] in CSV_DEFAULT_FIELDS
                ):
                    # Skip fields that were set as part of the user registration
                    continue

                if row[field] is None:
                    row[field] = ""

                setfield(data, row[field], fieldmap[field], autodate=autodate)
            indico.regedit(conference, regform, regid, data, notify)
        except IndicoCliException as e:
            tqdm.write(f"{row[emailfield]} FAILED: {e}")
        except Exception as e:
            tqdm.write(f"{row[emailfield]} FAILED: {type(e).__name__}: {e}")
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                raise e


@main.command()
@click.argument("conference", type=int)
@click.pass_obj
def submitcheck(indico, conference):
    """Check if all contributors have the submitter bit set"""
    indico.check_contrib_submitter(conference)


@main.command()
@click.argument("conference", type=int)
@click.pass_obj
def timetable(indico, conference):
    """Get timetable json data"""

    data = indico.get_timetable(conference)
    click.echo(json.dumps(data, indent=2))


@main.command()
@click.argument("conference", type=int)
@click.option(
    "--import",
    "importfile",
    type=click.File("r"),
    help="Import contributions to conference from file",
)
@click.pass_obj
def contributions(indico, conference, importfile):
    """Retrieve or import contributions json data"""

    def ensure_author(authormap, obj):
        if obj["id"] not in authormap:
            authormap[obj["id"]] = {
                "first_name": obj["first_name"],
                "last_name": obj["last_name"],
                "affiliation": obj["affiliation"],
                "affiliation_id": None,
                "email": obj["email"],
                "address": "",
                "phone": "",
                "roles": ["submitter"],
            }
        return authormap[obj["id"]]

    if importfile:
        contribdata = json.load(importfile)

        for contribution in contribdata["results"][0]["contributions"]:
            authormap = {}
            for speaker in contribution["speakers"]:
                author = ensure_author(authormap, speaker)
                if "speaker" not in author["roles"]:
                    author["roles"].append("speaker")
            for primaryauthor in contribution["primaryauthors"]:
                author = ensure_author(authormap, primaryauthor)
                if "primary" not in author["roles"]:
                    author["roles"].append("primary")
            for coauthor in contribution["coauthors"]:
                author = ensure_author(authormap, coauthor)
                if "secondary" not in author["roles"]:
                    author["roles"].append("secondary")

            data = {
                "title": contribution["title"],
                "description": contribution["description"],
                "duration": contribution["duration"] * 60,
                "title": contribution["title"],
                "person_link_data": json.dumps(list(authormap.values())),
                "location_data": '{"address":"","inheriting":true}',
                "references": "[]",
                "board_number": "",
                "code": "",
            }
            try:
                indico.add_contribution(conference, data)
            except:
                print("FAILED", json.dumps(data))

    else:
        data = indico.get_contributions(conference)
        click.echo(json.dumps(data, indent=2))


@main.command()
@click.argument("conference", type=int)
@click.argument("parent", type=int)
@click.option("--from", "fromContribution", help="Create from contribution")
@click.option("--title", help="The title to set")
@click.option("--description", help="The description to set")
@click.option("--duration", help="The duration to set")
@click.option("--speaker", help="The speaker data to set")
@click.pass_obj
def subcontribution(
    indico, conference, parent, fromContribution, title, description, duration, speaker
):
    if fromContribution:
        contrib = indico.get_contribution_entry(conference, fromContribution)
        title = contrib["title"]
        description = contrib["description"]
        duration = contrib["duration"]
        persons = []
        for person in contrib["persons"]:
            if person["is_speaker"]:
                persons.append(person)
                person["roles"] = ["speaker"]
                del person["is_speaker"]

    indico.create_subcontribution(
        conference,
        parent,
        {
            "title": title,
            "description": description,
            "duration": duration,
            "speakers": json.dumps(persons),
            "references": "[]",
            "code": contrib["code"] if fromContribution else "",
        },
    )


@main.command()
@click.argument("conference", type=int)
@click.argument("constraints", type=click.File())
@click.option("--noauthor", is_flag=True)
@click.option("--usecache", is_flag=True, help="Use contributions.json cache")
@click.pass_obj
def overlap(indico, conference, constraints, noauthor, usecache):
    """Check timetable overlap"""

    def get_contrib_url(contrib):
        return f"https://events.canonical.com/event/{conference}/contributions/{contrib[idprop]}"

    def get_contrib_date(contrib):
        return datetime.fromisoformat(
            contrib["startDate"]["date"] + "T" + contrib["startDate"]["time"]
        )

    data = yaml.safe_load(constraints)
    import operator
    from datetime import date, datetime
    from pprint import pprint

    import pytz

    results = indico.get_contributions(conference, cache=usecache, tz=data["timezone"])
    contributions = results["results"][0]["contributions"]
    timezone = pytz.timezone(data["timezone"])
    idprop = data.get("idprop", "id")

    contributions = {int(contrib[idprop]): contrib for contrib in contributions}

    for constraint in data["constraints"]:
        if constraint["id"] not in contributions:
            print(f"Failed: Could not find {constraint['id']}")
            continue
        contrib = contributions[constraint["id"]]
        contrib_date = get_contrib_date(contrib)
        contrib_url = get_contrib_url(contrib)
        failed = False

        print(f"Check: {contrib['title']}")

        if "room" in constraint:
            if constraint["room"] != contrib["room"]:
                print(f"\tFailed: {contrib_url} in the wrong room")
                print(f"\tShould be in {constraint['room']}")
            else:
                print(f"\tOK: is in {constraint['room']}")

        if "before" in constraint:
            if isinstance(constraint["before"], (date, datetime)):
                constraint_date = constraint["before"]

                if isinstance(constraint_date, date):
                    constraint_date = datetime(
                        year=constraint_date.year,
                        month=constraint_date.month,
                        day=constraint_date.day,
                    )

                if not (contrib_date < constraint_date):
                    print(f"\tFailed: {contrib_url} needs to be earlier")
                    print(f"\t!({contrib_date} < {constraint_date})")
                else:
                    print(f"\tOK: is before {constraint_date}")

            if isinstance(constraint["before"], int):
                othercontrib = contributions[constraint["before"]]
                other_contrib_date = get_contrib_date(other_contrib)

                if not (contrib_date < other_contrib_date):
                    print(
                        f"\tFailed: {contrib_url} is not before {get_contrib_url(other_contrib)}"
                    )
                    print(f"\t!({contrib_date} < {other_contrib_date})")
                else:
                    print(f"\tOK: is before {other_contrib['title']}")

        if "after" in constraint:
            if isinstance(constraint["after"], (date, datetime)):
                constraint_date = constraint["after"]

                if isinstance(constraint_date, date):
                    constraint_date = datetime(
                        year=constraint_date.year,
                        month=constraint_date.month,
                        day=constraint_date.day,
                    )

                if not (contrib_date > constraint_date):
                    print(f"\tFailed: {contrib_url} needs to be later")
                    print(f"\t!({contrib_date} > {constraint_date})")
                else:
                    print(f"\tOK: is after {constraint_date}")

            if isinstance(constraint["after"], int):
                other_contrib = contributions[constraint["after"]]
                other_contrib_date = get_contrib_date(other_contrib)

                if not (contrib_date > other_contrib_date):
                    print(
                        f"\tFailed: {contrib_url} is not after {get_contrib_url(other_contrib)}"
                    )
                    print(f"\t!({contrib_date} > {other_contrib_date})")
                else:
                    print(f"\tOK: is after {other_contrib['title']}")

    if not noauthor:
        print("Author conflicts:")
        indico.check_author_overlap(conference)


@main.command()
@click.argument("conference", type=int)
@click.argument("query")
@click.pass_obj
def emaillog(indico, conference, query):
    """Retrieve the email log

    Pass in the text to query for
    """

    data = indico.get_log(conference, query, logtype=["email"])
    click.echo(json.dumps(data, indent=2))


@main.command()
@click.argument("conference", type=int)
@click.argument("entryA", type=int)
@click.argument("entryB", type=int)
@click.option(
    "-t",
    "--type",
    "idtype",
    type=click.Choice(("cid", "tid", "aid")),
    default="cid",
    help="Type of id specified (contribution id, timetable id, aid)",
)
@click.pass_obj
def swap(indico, conference, entryA, entryB, idtype):
    """Swap timetable entries

    Pass in the id of the conference, and two ids related to the timetable that will be swapped.
    """

    keymap = {"cid": "contributionId", "tid": "id", "aid": "friendlyId"}
    data = indico.swap_timetable(conference, entryA, entryB, keymap[idtype])
    click.echo(json.dumps(data, indent=2))


@main.command()
@click.option("--prod", is_flag=True, help="Clear the production token")
@click.option("--stage", is_flag=True, help="Clear the production token")
def cleartoken(prod, stage):
    """Clear indico tokens.


    If neither --prod or --stage are passed, both tokens are cleared.
    """

    if not prod and not stage:
        prod = True
        stage = True

    if stage:
        try:
            keyring.delete_password("indico", "token.stage")
        except keyring.errors.PasswordDeleteError:
            pass
        click.echo("Stage token has been cleared")

    if prod:
        try:
            keyring.delete_password("indico", "token.prod")
        except keyring.errors.PasswordDeleteError:
            pass
        click.echo("Production token has been cleared")


@main.command()
@click.argument("conference", type=int)
@click.argument("contribId", type=int)
@click.argument("url")
@click.argument("title")
@click.pass_obj
def contrib_link(indico, conference, contribId, url, title):
    """Add a contribution link

    Pass in the conference id, the id of the contribution, the url fof the link to add, and the
    title of the link
    """
    if not url.startswith("http"):
        raise click.UsageError(f"{url} is not a link")

    click.echo(indico.contributions_link(conference, contribId, url, title))


if __name__ == "__main__":
    main()
