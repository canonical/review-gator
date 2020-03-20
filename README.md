Review Gator
=============

Review gator is an aggregate view of code reviews a team has to do.

It pulls github and launchpad for open review slots and generates a static HTML
review queue, that can be hosted anywhere. Review Gator works with Launchpad 
(bazaar or git) or Github repositories.

Installation
------------

The following steps should leave you with a working script:

```
virtualenv -p python3 --system-site-packages .venv
source .venv/bin/activate
pip install -e .
GITHUB_TOKEN=... review-gator --config branches.yaml
```

Basic Configuration
-------------------

Configuration is provided via a YAML file. To see an example, run 
`review-gator --config-skeleton`

```
launchpad:
    owners:
        # Launchpad username
        LAUNCHPAD-USERNAME:
            max-age: 30
    branches:
        # Full path to repo
        LAUNCHPAD-BZR-REPO-PATH:
            review-count: 2
github:
    repos:
        GITHUB-USERNAME:
            GITHUB-PROJECT-NAME:
                review-count: 2
lp-git:
    owners:
        LAUNCHPAD-USERNAME:
            max-age: 30
    repos:
        LAUNCHPAD-GIT-REPO-PATH:
            review-count: 2
```
NOTE: as of v. 0.2, `owners` and all options underneath are experimental 
with no guarantee of working. If you'd like to improve this work, we'll happily
take PRs!


Extras
------------

Review Gator has the ability to run tox on all your Python based codelines.
This can be enabled on a per repo:

```
github:
    repos:
        CanonicalLtd:
            review-gator:
                review-count: 2
                tox: true
```

To use this, tox must be installed in the environment. 

Furthermore, to use all the launchpad capabilities, launchpadlib must be installed.

If you have an existing install, and want to add the extras, run

`pip install .['extras']`

If you are starting from scratch, and want full capabilities, run

`pip install .['full']`
