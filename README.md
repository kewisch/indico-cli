# indico-cli

An assortment of i-need-this-to-get-something-done-now scripts for [Indico](https://getindico.io/).

This isn't considered a complete client for Indico by any means (though it could be some day?). Contribtions in that direction are very much welcome.


## Usage

```
git clone https://github.com/kewisch/indico-cli
cd indico-cli
pipenv install
pipenv run cli --help
```

```
usage: cli.py [-h] [-e {prod,stage}] [-d {DEBUG,INFO,WARNING,ERROR,CRITICAL}] subcommand

positional arguments:
  subcommand
                        cleartoken     Clear indico tokens
                        contrib_link   Add a contribution link
                        contributions  Get contributions json data
                        emaillog       Retrieve the email log
                        overlap        Check timetable overlap
                        submitcheck    Check if all contributors have the submitter bit set
                        swap           Swap timetable entries
                        timetable      Get timetable json data

options:
  -h, --help            show this help message and exit
  -e {prod,stage}, --env {prod,stage}
                        The environment to use
  -d {DEBUG,INFO,WARNING,ERROR,CRITICAL}, --debug {DEBUG,INFO,WARNING,ERROR,CRITICAL}
````
