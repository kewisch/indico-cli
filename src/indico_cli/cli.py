import csv
import getpass
import json
import logging
import sys

import click
import keyring
from tqdm import tqdm

from .indico import Indico
from .util import IndicoCliException, RegIdMap, fieldnamemap, init_logging, setfield

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
        fields = list(map(lambda field: fieldmap[field]["htmlName"], fields.split(",")))
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
@click.argument("conference", type=int)
@click.argument("regform", type=int)
@click.pass_obj
def regfields(indico, conference, regform):
    """Get field names for CSV import"""

    data = indico.regfields(conference, regform)
    click.echo(
        "When putting together the CSV file for import, use the Name of the field as"
    )
    click.echo(
        "the column header. You will only need the ID if you are using --rawfields\n"
    )
    for field, data in data.items():
        if not data["isEnabled"]:
            continue

        click.echo(
            f"     ID: {data['htmlName']:<10}   Type: {data['inputType']:<20} Name: {data['title']}"
        )
        if "captions" in data:
            click.echo("         Choices:")
            for uid, caption in data["captions"].items():
                click.echo(f"           {caption} ({uid})")
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
        return rawfieldmap[name]["htmlName" if rawfields else "title"]

    emailfield = lookupfield("email")

    reader = csv.DictReader(csvfile)
    if lookupfield("email") not in reader.fieldnames:
        raise IndicoCliException("Missing Email Address field in csv file")
    fieldnames = reader.fieldnames
    rows = list(reader)

    if register:
        for row in rows:
            if row[emailfield] not in cachereg:
                if lookupfield("first_name") in row and lookupfield("last_name") in row:
                    registerusers[row[lookupfield("email")]] = [
                        row[lookupfield("first_name")],
                        row[lookupfield("last_name")],
                        row[lookupfield("affiliation")]
                        if lookupfield("affiliation") in row
                        else "",
                        row[lookupfield("position")]
                        if lookupfield("position") in row
                        else "",
                        row[lookupfield("phone")]
                        if lookupfield("phone") in row
                        else "",
                        row[emailfield],
                    ]
                else:
                    raise IndicoCliException(
                        f"User {row[emailfield]} is not previously registered, CSV requires at "
                        + "least email, firstname and lastname fields, preferably also "
                        + "affiliation, position (team)"
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
                if row[emailfield] in registerusers and fieldmap[field]["htmlName"] in (
                    "first_name",
                    "last_name",
                    "affiliation",
                    "position",
                    "phone",
                ):
                    # Skip fields that were set as part of the user registration
                    continue
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
@click.pass_obj
def contribtions(indico, conference):
    """Get contributions json data"""

    data = indico.get_contributions(conference)
    click.echo(json.dumps(data, indent=2))


@main.command()
@click.argument("conference", type=int)
@click.pass_obj
def overlap(indico, conference):
    """Check timetable overlap"""

    indico.check_overlap(conference)


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
def cleartoken():
    """Clear indico tokens"""

    try:
        keyring.delete_password("indico", "token.stage")
    except keyring.errors.PasswordDeleteError:
        pass
    try:
        keyring.delete_password("indico", "token.prod")
    except keyring.errors.PasswordDeleteError:
        pass
    click.echo("Tokens have been cleared")


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
