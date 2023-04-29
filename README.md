# indico-cli

An assortment of i-need-this-to-get-something-done-now scripts for [Indico](https://getindico.io/).

This isn't considered a complete client for Indico by any means (though it could be some day?). Contribtions in that direction are very much welcome.


## Usage

```
git clone https://github.com/kewisch/indico-cli
cd indico-cli

pipx install --editable .[dev]
indico-cli --help
```

```
usage: cli.py [-h] [-e {prod,stage,local}] [-d {DEBUG,INFO,WARNING,ERROR,CRITICAL}] subcommand

positional arguments:
  subcommand
                        adduser        Provsion a user
                        cleartoken     Clear indico tokens
                        contrib_link   Add a contribution link
                        contributions  Get contributions json data
                        emaillog       Retrieve the email log
                        groupadduser   Adds a user to a group
                        overlap        Check timetable overlap
                        regedit        Edit a user registration
                        regeditcsv     Bulk edit user registration via csv
                        regfields      Get field names for CSV import
                        regquery       Query registrations by filter
                        submitcheck    Check if all contributors have the submitter bit set
                        swap           Swap timetable entries
                        timetable      Get timetable json data

options:
  -h, --help            show this help message and exit
  -e {prod,stage,local}, --env {prod,stage,local}
                        The environment to use
  -d {DEBUG,INFO,WARNING,ERROR,CRITICAL}, --debug {DEBUG,INFO,WARNING,ERROR,CRITICAL}
````
