import csv
import getpass
import http.client
import json
import logging
import sys

import keyring
from arghandler import ArgumentHandler, subcmd
from indico import Indico
from progress.bar import Bar
from progress.spinner import Spinner

INDICO_PROD_URL = "https://events.canonical.com"  # prod
INDICO_STAGE_URL = "https://events.staging.canonical.com"  # staging


def init_logging(level, _):
    logging.basicConfig()
    logging.getLogger().setLevel(level)
    requests_log = logging.getLogger("requests.packages.urllib3")
    requests_log.setLevel(level)
    requests_log.propagate = True

    if level == logging.DEBUG:
        http.client.HTTPConnection.debuglevel = 1


def setfield(data, fieldkey, fieldvalue):
    fieldname, fieldtype, *_ = fieldkey.split("|") + [None]

    if fieldtype == "BOOL":
        data[fieldname] = True if fieldvalue.lower() in ("yes", "true", "1") else False
    elif fieldtype == "CHOICE":
        data[fieldname] = {fieldvalue: 1}
    else:
        data[fieldname] = fieldvalue

    return data


def regidmap(indico, conference):
    return dict(
        map(
            lambda row: (row["personal_data"]["email"], row["registrant_id"]),
            indico.get_registrations(conference),
        )
    )


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
            raise Exception("Could not find group " + args.group)

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
        print("All users already in group {}".format(args.group))
    else:
        users.update(userids)
        indico.editgroup(groupid, list(users))


@subcmd("regbulk", help="Bulk register users, skipping those already existing")
def cmd_regbulk(handler, indico, args):
    handler.add_argument(
        "conference", type=int, help="The id of the conference to edit"
    )
    handler.add_argument(
        "regform", type=int, help="The id of the registration FORM to edit"
    )
    handler.add_argument(
        "--notify", action="store_true", help="Notify the user of the change"
    )
    handler.add_argument("csvfile", help="The file with the data")
    args = handler.parse_args(args)

    cachereg = regidmap(indico, args.conference)
    allusers = []

    with open(args.csvfile, newline="") as csvfile:
        reader = csv.DictReader(csvfile)

        extrafields = reader.fieldnames.copy()
        if "firstname" in extrafields:
            extrafields.remove("firstname")
        if "lastname" in extrafields:
            extrafields.remove("lastname")
        if "affiliation" in extrafields:
            extrafields.remove("affiliation")
        if "email" in extrafields:
            extrafields.remove("email")
        if "position" in extrafields:
            extrafields.remove("position")

        csvdatain = list(reader)

    # First pass, find users that are not yet registered and register them using a CSV import
    for row in csvdatain:
        if "email" not in row:
            raise Exception("CSV file at least requires an email address as key")
        if (
            row["email"] not in cachereg
            and "firstname" in row
            and "lastname" in row
            and "affiliation" in row
            and "position" in row
        ):
            allusers.append(
                [
                    row["firstname"],
                    row["lastname"],
                    row["affiliation"],
                    row["position"],
                    "",  # Phone number, we'll likely never needs this
                    row["email"],
                ]
            )
        elif row["email"] not in cachereg:
            raise Exception("User {} is not yet registered".format(row["email"]))

    if len(allusers) > 0:
        with Spinner("Registering {} new users".format(len(allusers))) as spinner:
            indico.regcsvimport(
                args.conference, args.regform, allusers, notify=args.notify
            )
            spinner.next()

        # Reload cache to get new reg ids
        cachereg = regidmap(indico, args.conference)

    # For any extra fields, update the registrations with the new data
    if len(extrafields):
        with Bar("Updating user data", max=len(csvdatain)) as bar:
            for row in csvdatain:
                regid = cachereg[row["email"]]
                data = {}
                for field in extrafields:
                    setfield(data, field, row[field])

                try:
                    indico.regedit(
                        args.conference, args.regform, regid, data, notify=args.notify
                    )
                except Exception as e:
                    print(regid, "FAILED", e)
                bar.next()


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
        "--setbool",
        nargs=2,
        action="append",
        default=[],
        metavar=("fieldid", "value"),
        help="Set a boolean field (yes,true,1 = True ; anything else = False)",
    )
    handler.add_argument(
        "--settext",
        nargs=2,
        action="append",
        default=[],
        metavar=("fieldid", "value"),
        help="Set a text field",
    )
    handler.add_argument(
        "--setchoice",
        nargs=2,
        action="append",
        default=[],
        metavar=("fieldid", "guid"),
        help="Set a choice field (guid of choice required)",
    )
    handler.add_argument(
        "--notify", action="store_true", help="Notify the user of the change"
    )
    handler.add_argument(
        "--all", "-a", action="store_true", help="Set the value on all registrants"
    )
    args = handler.parse_args(args)

    if args.all:
        print("Retrieving all registration ids", end="", flush=True)
        args.regid = list(
            map(
                lambda row: row["registrant_id"],
                indico.get_registrations(args.conference),
            )
        )
        print("Done")
    else:
        print("Looking up emails...", end="", flush=True)
        cachereg = regidmap(indico, args.conference)
        try:
            args.regid = list(
                map(
                    lambda regid: int(regid) if regid.isdigit() else cachereg[regid],
                    args.regid,
                )
            )
        except KeyError as e:
            print("{} not found".format(e.args[0]))
            sys.exit(1)
        print("Done")

    with Bar("Setting fields", max=len(args.regid)) as bar:
        for regid in args.regid:
            data = {}
            for key, value in args.settext:
                data[key] = value

            for key, value in args.setbool:
                data[key] = True if value.lower() in ("yes", "true", "1") else False

            for key, value in args.setchoice:
                data[key] = {value: 1}

            try:
                indico.regedit(args.conference, args.regform, regid, data, args.notify)
            except Exception as e:
                print(regid, "FAILED", e)
            bar.next()


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
    for field, title in data.items():
        print("{0:<12}: {1}".format(field, title))

    print(
        """
        With this information you can create the heading for the CSV file.
        When you are registering new users, you need to have the following fields:
          * firstname
          * lastname
          * affiliation      (This is the company, e.g. Canonical)
          * position         (This is used for the team)
          * email

        email,firstname,lastname,affiliation,position,field_85,field_234|BOOL,field_233|BOOL,field_235|BOOL
        user@example.com,John,Doe,Canonical,Community,REQ-123123,yes,yes,no
        user2@example.com,Jane,Doe,Canonical,MAAS,REQ-232324,no,yes,no

        If you are certain those users are already registered, you can provide just the email field.

        email,field_234|BOOL,field_233|BOOL,field_235|BOOL
        user@example.com,yes,yes,no
        user2@example.com,no,yes,no

        What is easy to set at this point is text fields and Yes/No choices. Setting fields that
        have multiple choices such as shirt size require some additional code I haven't written

        Then run:

        pipenv run cli regbulk <conference> <regform> mycsvfile.csv
    """
    )


@subcmd("regeditcsv", help="Bulk edit user registration via csv")
def cmd_regeditcsv(handler, indico, args):
    handler.add_argument(
        "conference", type=int, help="The id of the conference to edit"
    )
    handler.add_argument(
        "regform", type=int, help="The id of the registration FORM to edit"
    )
    handler.add_argument(
        "--notify", action="store_true", help="Notify the user of the change"
    )
    handler.add_argument("csvfile", help="The file with the data")
    args = handler.parse_args(args)

    with open(args.csvfile, newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        cachereg = None
        if "email" not in reader.fieldnames and "regid" not in reader.fieldnames:
            raise Exception("Missing email or reg id in reg file")
        if "regid" not in reader.fieldnames:
            cachereg = dict(
                map(
                    lambda row: (row["personal_data"]["email"], row["registrant_id"]),
                    indico.get_registrations(args.conference),
                )
            )

        with Bar("Setting fields", max=len(args.regid)) as bar:
            for row in reader:
                regid = row["regid"] if "regid" in row else cachereg[row["email"]]

                data = {}
                for field in reader.fieldnames:
                    if field == "email" and "regid" not in row:
                        continue
                    setfield(data, field, row[field])

                try:
                    indico.regedit(
                        args.conference, args.regform, regid, data, args.notify
                    )
                except Exception as e:
                    print(regid, "FAILED", e)
                bar.next()


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
    keyring.delete_password("indico", "token.stage")
    keyring.delete_password("indico", "token.prod")
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
            token = getpass.getpass("Enter token for {}: ".format(args.env))
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
