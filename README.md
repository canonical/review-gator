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

Note: The code for the owners stanza is immature and requires more testing and
development. 

### Configuration Explanation and Hierarchy

#### Hosting & Repository Type type:
    
* `launchpad`: Launchpad hosted bazaar repositories
* `github`: Github hosted git repositories
* `lp-git`: Launchpad hosted git repositories

#### Owners or Repos:

* `owners`: Checks for all MPs under an owners specfied below.
* `repos`: Check for MPs for listed repos

#### User or Repo

* `$LAUNCHPAD-USERNAME`: User to search for for MPs
* `$REPO-NAME`: Repository name for searching for MPs

#### Specific Configurations
* `owners.$USERNAME.max-age`: Int. Max age of the MP to show in search results
* `repos.$REPO-NAME.review-count`: Int. Number of reviews to show in the search results
* `repos.$REPO-NAME.tox`: Boolean. Enable tox for a specific repository

Testing with Tox
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

Tox is run in parallel using `--tox-jobs`. By default, Review Gator will parallelize 
as many jobs as possible. If you'd like to limit parellization, pass an into to `--tox-jobs` 
to set the max number of jobs

TODO
-----

* Test `owners` stanza support to identify and document issues, and 
  complete `owners` support
* Add unit testing.