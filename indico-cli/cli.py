import csv
import getpass
import json
import logging
import sys

import keyring
from arghandler import ArgumentHandler, subcmd
from indico import Indico
from tqdm import tqdm
from util import IndicoCliException, RegIdMap, fieldnamemap, init_logging, setfield

INDICO_PROD_URL = "https://events.canonical.com"  # prod
INDICO_STAGE_URL = "https://events.staging.canonical.com"  # staging


@subcmd("adduser", help="Provsion a user")
def cmd_adduser(handler, indico, args):
    handler.add_argument("email", help="The email name of the user")
    handler.add_argument("firstname", help="The first name of the user")
    handler.add_argument("familyname", help="The family  name of the user")
    handler.add_argument("affiliation", nargs="?", help="The affiliation of the user")
    args = handler.parse_args(args)

    indico.adduser(args.email, args.firstname, args.familyname, args.affiliation)


@subcmd("groupadduser", help="Adds a user to a group")
def cmd_groupadduser(handler, indico, args):
    handler.add_argument("group", help="The id or name of the group")
    handler.add_argument("user", nargs="+", help="The id or email of the user")
    args = handler.parse_args(args)

    if args.group.isdigit():
        groupid = int(args.group)
    else:
        groupdata = indico.searchgroup(args.group)
        if len(groupdata) == 1:
            groupid = groupdata[0]["id"]
        else:
            raise IndicoCliException("Could not find group " + args.group)

    userids = set()
    for user in args.user:
        if user.isdigit():
            userids.add(int(user))
        else:
            userdata = indico.searchuser(user)
            if len(userdata) == 0:
                print("Warning: Could not find user " + user)
            else:
                userids.add(userdata[0]["id"])

    users = set(indico.getgroupusers(groupid))
    if userids.issubset(users):
        print(f"All users already in group {args.group}")
    else:
        users.update(userids)
        indico.editgroup(groupid, list(users))


@subcmd("regedit", help="Edit a user registration")
def cmd_regedit(handler, indico, args):
    handler.add_argument(
        "conference", type=int, help="The id of the conference to edit"
    )
    handler.add_argument(
        "regform", type=int, help="The id of the registration FORM to edit"
    )
    handler.add_argument(
        "regid", nargs="*", help="The registration id or email to edit"
    )
    handler.add_argument(
        "--set",
        "-s",
        nargs=2,
        dest="setfields",
        action="append",
        default=[],
        metavar=("fieldname", "value"),
        help="Set a field",
    )
    handler.add_argument(
        "--allow-email", action="store_true", help="Allow changing the Email field"
    )
    handler.add_argument(
        "--rawfields",
        action="store_true",
        help="Assume the CSV is using raw field names",
    )
    handler.add_argument(
        "--autodate", action="store_true", help="Automatically parse date formats"
    )
    handler.add_argument(
        "--notify", action="store_true", help="Notify the user of the change"
    )
    handler.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="Set the value on all registrants. CAVEAT: this only works with a single registration form",
    )
    args = handler.parse_args(args)

    if args.all:
        print("Retrieving all registration ids...", end="", flush=True)
        args.regid = list(
            map(
                lambda row: row["registrant_id"],
                indico.get_registrations(args.conference),
            )
        )
        print("Done")
    else:
        cachereg = RegIdMap(indico, args.conference)
        try:
            args.regid = list(
                map(
                    lambda regid: int(regid) if regid.isdigit() else cachereg[regid],
                    args.regid,
                )
            )
        except KeyError as e:
            print(f"{e.args[0]} not found")
            sys.exit(1)

    fieldinfo = indico.regfields(args.conference, args.regform)
    fieldmap, rawfieldmap = fieldnamemap(fieldinfo, rawfields=args.rawfields)
    for regid in tqdm(args.regid, desc="Setting fields", unit="users"):
        data = {}

        try:
            for key, value in args.setfields:
                setfield(
                    data,
                    value,
                    fieldmap[key],
                    autodate=args.autodate,
                    allow_email=args.allow_email,
                )

            indico.regedit(args.conference, args.regform, regid, data, args.notify)
        except IndicoCliException as e:
            tqdm.write(f"{args.regid} FAILED: {e}")
        except Exception as e:
            tqdm.write(f"{args.regid} FAILED: {type(e).__name__}: {e}")
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                raise e


@subcmd("regfields", help="Get field names for CSV import")
def cmd_regfields(handler, indico, args):
    handler.add_argument(
        "conference", type=int, help="The id of the conference to edit"
    )
    handler.add_argument(
        "regform", type=int, help="The id of the registration FORM to edit"
    )
    args = handler.parse_args(args)

    data = indico.regfields(args.conference, args.regform)
    print("When putting together the CSV file for import, use the Name of the field as")
    print("the column header. You will only need the ID if you are using --rawfields\n")
    for field, data in data.items():
        if not data["isEnabled"]:
            continue

        print(
            f"     ID: {data['htmlName']:<10}   Type: {data['inputType']:<20} Name: {data['title']}"
        )
        if "captions" in data:
            print("         Choices:")
            for uid, caption in data["captions"].items():
                print(f"           {caption} ({uid})")
            print("\n")


@subcmd("regeditcsv", help="Bulk edit user registration via csv")
def cmd_regeditcsv(handler, indico, args):
    handler.add_argument(
        "conference", type=int, help="The id of the conference to edit"
    )
    handler.add_argument(
        "regform", type=int, help="The id of the registration FORM to edit"
    )
    handler.add_argument(
        "--register", action="store_true", help="Register users if they don't exist"
    )
    handler.add_argument(
        "--autodate", action="store_true", help="Automatically parse date formats"
    )
    handler.add_argument(
        "--rawfields",
        action="store_true",
        help="Assume the CSV is using raw field names",
    )
    handler.add_argument(
        "--notify", action="store_true", help="Notify the user of the change"
    )
    handler.add_argument("csvfile", help="The file with the data")
    args = handler.parse_args(args)

    print("Loading field and registration data...", end="", flush=True)
    fieldinfo = indico.regfields(args.conference, args.regform)
    fieldmap, rawfieldmap = fieldnamemap(fieldinfo, args.rawfields)
    cachereg = RegIdMap(indico, args.conference, noisy=False)
    print("Done")

    fieldnames = None
    rows = None
    registerusers = {}

    def lookupfield(name):
        return rawfieldmap[name]["htmlName" if args.rawfields else "title"]

    emailfield = lookupfield("email")

    with open(args.csvfile, newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        if lookupfield("email") not in reader.fieldnames:
            raise IndicoCliException("Missing Email Address field in csv file")
        fieldnames = reader.fieldnames
        rows = list(reader)

    if args.register:
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
            print(f"Registering {len(registerusers)} new users...", end="", flush=True)
            indico.regcsvimport(
                args.conference,
                args.regform,
                registerusers.values(),
                notify=args.notify,
            )
            print("Done")
            # Reload cache to get new reg ids
            cachereg = RegIdMap(indico, args.conference)

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
                setfield(data, row[field], fieldmap[field], autodate=args.autodate)
            indico.regedit(args.conference, args.regform, regid, data, args.notify)
        except IndicoCliException as e:
            tqdm.write(f"{row[emailfield]} FAILED: {e}")
        except Exception as e:
            tqdm.write(f"{row[emailfield]} FAILED: {type(e).__name__}: {e}")
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                raise e


@subcmd("submitcheck", help="Check if all contributors have the submitter bit set")
def cmd_submitcheck(handler, indico, args):
    handler.add_argument(
        "conference", type=int, help="The id of the conference to check"
    )
    args = handler.parse_args(args)

    indico.check_contrib_submitter(args.conference)


@subcmd("timetable", help="Get timetable json data")
def cmd_timetable(handler, indico, args):
    handler.add_argument("conference", type=int, help="The id of the conference")
    args = handler.parse_args(args)

    data = indico.get_timetable(args.conference)
    print(json.dumps(data, indent=2))


@subcmd("contributions", help="Get contributions json data")
def cmd_contribtions(handler, indico, args):
    handler.add_argument("conference", type=int, help="The id of the conference")
    args = handler.parse_args(args)

    data = indico.get_contributions(args.conference)
    print(json.dumps(data, indent=2))


@subcmd("overlap", help="Check timetable overlap")
def cmd_overlap(handler, indico, args):
    handler.add_argument("conference", type=int, help="The id of the conference")
    args = handler.parse_args(args)

    indico.check_overlap(args.conference)


@subcmd("emaillog", help="Retrieve the email log")
def cmd_emaillog(handler, indico, args):
    handler.add_argument("conference", type=int, help="The id of the conference")
    handler.add_argument("query", help="The text to search for in the log")
    args = handler.parse_args(args)

    data = indico.get_log(args.conference, args.query, logtype=["email"])
    print(json.dumps(data, indent=2))


@subcmd("swap", help="Swap timetable entries")
def cmd_swap(handler, indico, args):
    handler.add_argument(
        "-t",
        "--type",
        choices=("cid", "tid", "aid"),
        default="cid",
        help="Type of id specified (contribution id, timetable id, aid)",
    )
    handler.add_argument("conference", type=int, help="The id of the conference")
    handler.add_argument("entryA", type=int, help="The id the first entry")
    handler.add_argument("entryB", type=int, help="The id the second entry")
    args = handler.parse_args(args)

    keymap = {"cid": "contributionId", "tid": "id", "aid": "friendlyId"}
    data = indico.swap_timetable(
        args.conference, args.entryA, args.entryB, keymap[args.type]
    )
    print(json.dumps(data, indent=2))


@subcmd("cleartoken", help="Clear indico tokens")
def cmd_cleartoken(handler, indico, args):
    try:
        keyring.delete_password("indico", "token.stage")
    except keyring.errors.PasswordDeleteError:
        pass
    try:
        keyring.delete_password("indico", "token.prod")
    except keyring.errors.PasswordDeleteError:
        pass
    print("Tokens have been cleared")


@subcmd("contrib_link", help="Add a contribution link")
def cmd_contrib_link(handler, indico, args):
    handler.add_argument(
        "-t",
        "--type",
        choices=("cid", "tid", "aid"),
        default="cid",
        help="Type of id specified (contribution id, timetable id, aid)",
    )
    handler.add_argument("conference", type=int, help="The id of the conference")
    handler.add_argument("contribId", type=int, help="The contribution id")
    handler.add_argument("url", help="The link to add")
    handler.add_argument("title", help="The title of the link")
    args = handler.parse_args(args)

    if not args.url.startswith("http"):
        print("not a link", args.url)
        sys.exit(1)

    print(
        indico.contributions_link(args.conference, args.contribId, args.url, args.title)
    )


def main():
    def load_context(args):
        token = keyring.get_password("indico", "token." + args.env)
        if token is None:
            token = getpass.getpass(f"Enter token for {args.env}: ")
            keyring.set_password("indico", "token." + args.env, token)

        if args.env == "prod":
            return Indico(INDICO_PROD_URL, token)
        elif args.env == "stage":
            return Indico(INDICO_STAGE_URL, token)

        raise Exception("Invalid environment " + args.env)

    handler = ArgumentHandler(use_subcommand_help=True)
    handler.add_argument(
        "-e",
        "--env",
        choices=("prod", "stage"),
        default="prod",
        help="The environment to use",
    )
    handler.set_logging_argument(
        "-d", "--debug", default_level=logging.WARNING, config_fxn=init_logging
    )

    try:
        handler.run(sys.argv[1:], context_fxn=load_context)
    except KeyboardInterrupt:
        pass


main()
