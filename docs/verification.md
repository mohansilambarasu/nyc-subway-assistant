# Product verification

## Automated checks

Run from the project folder:

```sh
.venv/bin/python -m unittest discover -s tests
node --test tests/*.cjs
```

Node is needed only for frontend development checks, not to run the application.

Coverage includes location matching and confirmation, entrance choices, valid map pins, route guidance, live prediction freshness, saved trips, missed-stop recovery, HTML escaping, and search-form interactions.

## Local-service checks

With the application and local routing/model services running:

```sh
.venv/bin/python scripts/map_qa.py
.venv/bin/python scripts/smoke_test.py
```

These commands generate ignored result files in this directory. The map QA command covers the public map workflow. The smoke command exercises internal conversational tools retained in the backend; chat is not available in the interface.

## Browser walkthrough

Start journey → search Joe’s Pizza → choose 1435 Broadway → search Charging Bull → choose the destination → Find routes → Start this route. Check A/B markers, automatic location zoom, editing, Hide search / Show search, train direction, journey progress, recovery, and saved trip restoration. Repeat at desktop and phone widths.

## Verification limits

Software checks do not verify physical station entrances, indoor passages, accessibility, or real-time closures. Routes use the downloaded timetable; arrivals and service notices are displayed separately.
