import getpass
import http.client
import json
import logging
import sys

import keyring
from arghandler import ArgumentHandler, subcmd
from indico import Indico

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
