#!/usr/bin/env python

import datetime
import os
import shutil
import socket
import sys
import time

import click
import github
from github.GithubException import (
    UnknownObjectException,
    RateLimitExceededException)
import humanize
import lazr.restfulclient.errors
import pytz
import yaml

from babel.dates import format_datetime
from jinja2 import Environment, FileSystemLoader
from joblib import Parallel, delayed

from lpshipit import _format_git_branch_name
from pkg_resources import resource_filename

from . import tox_runner
from . import clicklib
from .reporters import REPORTER_CLASSES

MAX_DESCRIPTION_LENGTH = 80

def print_warning(warning_msgs):
    """
    standard way to print a light scream
    Tell me what to scream about, and if you want to say more
    List[str]: warning_msgs List of strings of messages to display
    """
    print("**** WARNING ****")
    for msg in warning_msgs:
        print("** {} **".format(msg))

def localize_datetime(_datetime):
    """
    conditionally localize a datetime object if the datetime object is not
    already localized
    :param _datetime:
    :return:
    """
    if _datetime.tzinfo is None:
        return pytz.utc.localize(_datetime)
    else:
        return _datetime

NOW = localize_datetime(datetime.datetime.utcnow())


class Repo(object):
    '''Base class for a source code repository.

    These are the github repository or launchpad branch  that a pull request
    will target. A repo contain 0 or more pull requests.'''

    def __init__(self, repo_type, handle, url, name, dedicated_tab_name=None):
        self.repo_type = repo_type
        self.handle = handle
        self.url = url
        self.name = name
        self.pull_requests = []
        self.pull_requests_requiring_tox = []
        self.parallel_tox = True
        self.tab_name = dedicated_tab_name
        self.tox = False

    def __repr__(self):
        return 'Repo[{}, {}, {}, {}]'.format(
            self.repo_type, self.name, self.url, self.pull_requests)

    @property
    def pull_request_count(self):
        return len(self.pull_requests)

    @property
    def pull_request_requiring_tox_count(self):
        return len(self.pull_requests_requiring_tox)

    def add(self, pull_request):
        '''Add a pull request to this repository.'''
        self.pull_requests.append(pull_request)

    def add_requiring_tox(self, pull_request_requiring_tox, environment):
        '''Add a pull request that requires tox to this repository.'''
        self.pull_requests_requiring_tox.append((pull_request_requiring_tox, environment))


class GithubRepo(Repo):
    '''A github repository.'''
    def __init__(self, handle, url, name, dedicated_tab_name=None):
        super(GithubRepo, self).__init__('github', handle, url, name, dedicated_tab_name=dedicated_tab_name)


class LaunchpadRepo(Repo):
    '''A launchpad repository (aka branch).'''
    def __init__(self, handle, url, name, dedicated_tab_name=None):
        super(LaunchpadRepo, self).__init__('launchpad', handle, url, name, dedicated_tab_name=dedicated_tab_name)


class PullRequest(object):
    '''Base class for a request to merge into a repository.

    Represents a github pull request or launchpad merge proposal.'''
    def __init__(self, pull_request_type, handle, url, title, owner, state,
                 date, review_count, latest_activity=None):
        self.pull_request_type = pull_request_type
        self.handle = handle
        self.url = url
        self.title = title
        self.owner = owner
        self.state = state
        self.latest_activity = latest_activity
        self.date = date
        self.review_count = review_count
        self.reviews = []

    def __repr__(self):
        return u'PullRequest[{}, {}, {}, {}, {}]'.format(
            self.pull_request_type, self.title, self.owner, self.state,
            self.date)

    @property
    def age(self):
        # print(u'{}'.format(self))
        return date_to_age(self.date)

    @property
    def latest_activity_age(self):
        # print(u'{}'.format(self))
        if self.latest_activity is not None:
            return date_to_age(self.latest_activity)
        return self.age

    @property
    def mp_id(self):
        return self.url.split('/')[-1]

    def add_review(self, review):
        '''Adds a review, replacing any older review by the same owner.'''
        for idx, r in enumerate(self.reviews):
            if review.owner == r['owner'] and review.date > r['date']:
                del self.reviews[idx]
                break
        self.reviews.append(merge_two_dicts(review.__dict__,
                                            {'age': review.age}))


class GithubPullRequest(PullRequest):
    '''A github pull request.'''
    def __init__(self, handle, url, title, owner, state, date, review_count,
                 latest_activity=None):
        date = localize_datetime(date)
        super(GithubPullRequest, self).__init__(
                'github', handle, url, title, owner, state, date, review_count,
                latest_activity=latest_activity)


class LaunchpadPullRequest(PullRequest):
    '''A launchpad pull request (aka merge proposal).'''
    def __init__(self, handle, url, title, owner, state, date, review_count,
                 latest_activity=None):
        super(LaunchpadPullRequest, self).__init__(
                'launchpad', handle, url, title, owner, state, date,
                review_count, latest_activity=latest_activity)


class Review(object):
    '''A completed or requested review attached to a pull request.'''
    def __init__(self, review_type, review, url, owner, state, date):
        self.review_type = review_type
        self.review = review
        self.url = url
        self.owner = owner
        self.state = state
        self.date = date

    def __repr__(self):
        return u'Review[{}, {}, {}, {}, {}]'.format(self.review_type,
            self.review, self.owner, self.state, self.date)

    @property
    def age(self):
        # print(u'{}'.format(self))
        return date_to_age(self.date)


class GithubReview(Review):

    '''A github pull request review.'''
    def __init__(self, handle, url, owner, state, date):
        date = localize_datetime(date)

        super(GithubReview, self).__init__(
            'github', handle, url, owner, state, date)


class LaunchpadReview(Review):
    '''A launchpad merge proposal review.'''
    def __init__(self, handle, url, owner, state, date):
        super(LaunchpadReview, self).__init__(
            'launchpad', handle, url, owner, state, date)


def date_to_age(date):
    if date is None:
        return None
    if date == '':
        return None

    age = NOW - date
    if age < datetime.timedelta():
        # A negative timedelta means the time is in the future; this will be
        # due to inconsistent clocks across systems, so assume that there is no
        # delta
        age = datetime.timedelta()
    return humanize.naturaltime(age)


def merge_two_dicts(x, y):
    """Given two dicts, merge them into a new dict as a shallow copy."""
    z = x.copy()
    z.update(y)
    return z


def get_all_repos(gh, sources):
    '''Return all repos, prs and reviews for the given github sources.'''
    repos = []
    for org in sources:
        for name, data in sources[org].items():
            repo_name = '{}/{}'.format(org.replace(' ', ''), name)
            try:
                repo = gh.get_repo(repo_name)
            except UnknownObjectException:
                print_warning(
                    ["{} WAS NOT FOUND".format(repo_name),
                     "CHECK CREDENTIALS AND REPO NAME"]
                )
                continue
            except RateLimitExceededException as rle:
                print_warning(
                    ["Rate Limit Exception!",
                     str(rle)]
                )
            review_count = sources[org][name]['review-count']
            dedicated_tab_name = sources[org][name].get('tab-name', None)
            gr = GithubRepo(repo, repo.html_url, repo.ssh_url, dedicated_tab_name=dedicated_tab_name)
            gr.tox = sources[org][name].get('tox', False)
            gr.parallel_tox = sources[org][name].get('parallel-tox', True)
            gr.environment = sources[org][name].get('environment', None)
            get_prs(gr, repo, review_count, dedicated_tab_name)
            if gr.pull_request_count > 0:
                repos.append(gr)
            print(gr)
    return repos


def get_prs(gr, repo, review_count, dedicated_tab_name=None):
    '''Return all pull request for the given repository.'''
    pull_requests = []
    pulls = repo.get_pulls()
    for p in pulls:
        pr = GithubPullRequest(p, p.html_url, p.title, p.user.login,
                            p.state, p.created_at, review_count, dedicated_tab_name)
        gr.add(pr)
        pull_requests.append(pr)
        raw_reviews = p.get_reviews()
        raw_comments = p.get_comments()
        raw_issue_comments = p.get_issue_comments()
        pr_latest_activity = localize_datetime(p.created_at)

        # Find most recent issue comment activity on pull request
        for raw_issue_comment in raw_issue_comments:
            issue_comment_created_at = localize_datetime(
                raw_issue_comment.created_at)
            if pr_latest_activity is None or (
                    issue_comment_created_at > pr_latest_activity):
                pr_latest_activity = issue_comment_created_at

        # Find most recent comment activity on pull request
        for raw_comment in raw_comments:
            comment_created_at = localize_datetime(
                raw_comment.created_at)
            if pr_latest_activity is None or (
                    comment_created_at > pr_latest_activity):
                pr_latest_activity = comment_created_at

        for raw_review in raw_reviews:
            if raw_review.state == 'PENDING':
                continue
            owner = raw_review.user.login
            review = GithubReview(raw_review, raw_review.html_url, owner,
                                  raw_review.state, raw_review.submitted_at)
            pr.add_review(review)
            review_date = localize_datetime(raw_review.submitted_at)
            # Review might be more recent than a comment
            if pr_latest_activity is None or review_date > pr_latest_activity:
                pr_latest_activity = review_date

        pr.latest_activity = pr_latest_activity

    return pull_requests


def get_pr_data(pull_requests):
    '''Render the list of provided pull_requests.'''
    pr_data = []
    for p in pull_requests:
        pr_data.append(merge_two_dicts(p.__dict__, {
            'age': p.age,
            'id': p.mp_id,
            'latest_activity_age': p.latest_activity_age}))
    return pr_data


def get_repo_data(repos):
    '''Render the list of repos, their prs and reviews into an html table.'''
    repo_data = {}
    for repo in repos:
        repo_data[repo.name] = {
            'repo_url': repo.url,
            'repo_name': repo.name,
            'tox': repo.tox,
            'repo_shortname': repo.name.split('/')[-1],
            'pull_requests': get_pr_data(repo.pull_requests),
            'tab_name': repo.tab_name,
        }
    repo_data['dedicated_tabs'] = [repo.get('tab_name') for repo in repo_data.values() if repo.get('tab_name', None)]
    return repo_data


def report_repo_data(data):
    for reporter_cls in REPORTER_CLASSES:
        if reporter_cls.enabled():
            reporter_cls().process_data(data)


def render(repos, output_directory, tox):
    '''Render the repositories into an html file.'''
    data = get_repo_data(repos)
    report_repo_data(data)
    abs_templates_path = os.path.join(os.path.dirname(
            os.path.realpath(__file__)), "templates")
    abs_vendor_path = os.path.join(os.path.dirname(
            os.path.realpath(__file__)), "vendor")
    env = Environment(loader=FileSystemLoader(abs_templates_path))
    tmpl = env.get_template('reviews.html')

    # Make sure the output directory exists
    os.makedirs(output_directory, exist_ok=True)
    output_html_filepath = os.path.join(output_directory, 'reviews.html')
    with open(output_html_filepath, 'w') as out_file:
        context = {'repos': data, 'generation_time': NOW, 'tox': tox}
        out_file.write(tmpl.render(context))
        print("**** {} written ****".format(output_html_filepath))
        print("file://{}".format(output_html_filepath))
    output_vendor_dir = os.path.join(output_directory, 'vendor')
    shutil.rmtree(output_vendor_dir, True)
    # Copy the vendored CSS and JS
    shutil.copytree(abs_vendor_path, output_vendor_dir)


def get_mp_title(mp):
    '''Format a sensible MP title from git branches and the description.'''
    title = ''
    git_source = mp.source_git_path
    if git_source is not None:
        source = '<strong>'
        source += mp.source_git_repository_link.replace(
            'https://api.launchpad.net/devel/', '')
        source += ':' + git_source.replace('refs/heads/', '') + \
                  '</strong> &rArr; '
        title += source
    else:
        source = '<strong>'
        source += mp.source_branch_link.replace(
            'https://api.launchpad.net/devel/', '')
        source += '</strong> &rArr; '
        title += source
    git_target = mp.target_git_path
    if git_target is not None:
        target = mp.target_git_repository_link.replace(
            'https://api.launchpad.net/devel/', '')
        target += ':' + git_target.replace('refs/heads/', '')
        title += target
    else:
        target = mp.target_branch_link.replace(
            'https://api.launchpad.net/devel/', '')
        title += target

    description = mp.description
    if description is not None:
        description = description.split('\n')[0]
        if len(description) > MAX_DESCRIPTION_LENGTH:
            description = description[:MAX_DESCRIPTION_LENGTH] + '...'
        if len(title) > 0:
            title += '\n'
        title += description
    return title


def get_candidate_mps(branch):
    try:
        mps = branch.getMergeProposals(status='Needs review')
        mps.extend(branch.getMergeProposals(status='Work in progress'))
    except AttributeError:
        mps = branch.landing_candidates
    return mps


def get_mps(repo, branch, output_directory=None):
    '''Return all merge proposals for the given branch.'''
    mps = get_candidate_mps(branch)
    tox_mps = []
    for mp in mps:
        _, owner = mp.registrant_link.split('~')
        title = get_mp_title(mp)

        pr = LaunchpadPullRequest(mp, mp.web_link, title, owner,
                                  mp.queue_status,
                                  mp.date_created, 2)
        repo.add(pr)
        mp_latest_activity = None

        if repo.tox and pr.state == 'Needs review':
            repo.add_requiring_tox(mp, repo.environment)

        # Find most recent activity on merge proposal
        for mp_comment in mp.all_comments:
            if mp_latest_activity is None or \
                            mp_comment.date_created > mp_latest_activity:
                mp_latest_activity = mp_comment.date_created

        for vote in mp.votes:
            owner = vote.reviewer.display_name
            comment = vote.comment
            result = 'EMPTY'
            review_date = vote.date_created
            try:
                if comment is not None:
                    result = comment.vote
                    review_date = comment.date_created
            except lazr.restfulclient.errors.NotFound as comment_not_found_exception:
                print("Warning: MP ({}) could not find comment for vote from {} - "
                      "comment was likely deleted.".format(mp.web_link, owner))
            review = LaunchpadReview(vote, vote.web_link, owner, result,
                                     review_date)

            # MP Vote might be more recent than a comment
            if mp_latest_activity is None or review_date > mp_latest_activity:
                mp_latest_activity = review_date
            pr.add_review(review)

        pr.latest_activity = mp_latest_activity


def get_branches_for_owner(lp, collected, owner, max_age):
    '''Return all repos and prs for the given owner with the age limit.

    This is used to identify any recently submitted prs that escaped the
    whitelist of launchpad repositories. This only applies to launchpad.'''
    age_gate = NOW - datetime.timedelta(days=max_age)
    team = lp.people(owner)
    branches = team.getBranches(modified_since=age_gate)
    repos = []
    for b in branches:
        # XXX: Add logic to skip branches we already have
        if b.display_name in collected:
            continue
        branch = LaunchpadRepo(b, b.web_link, b.display_name)
        get_mps(branch, b)
        if branch.pull_request_count > 0:
            repos.append(branch)
    return repos


def get_branches(sources, lp_credentials_store=None):
    '''Return all repos, prs and reviews for the given launchpad sources.'''
    # deferred import of launchpadagent until required
    from . import launchpadagent
    cachedir_prefix = os.environ.get('SNAP_USER_COMMON', "/tmp")
    launchpad_cachedir = os.path.join('{}/get_reviews/.launchpadlib'.format(cachedir_prefix))
    # deferred import of launchpadagent until required
    from . import launchpadagent
    lp = launchpadagent.get_launchpad(
        launchpadlib_dir=launchpad_cachedir,
        lp_credentials_store=lp_credentials_store)
    repos = []
    for source, data in sources['branches'].items():
        print(source, data)
        b = lp.branches.getByUrl(url=source)
        try:
            repo = LaunchpadRepo(b, b.web_link, b.display_name)
        except AttributeError:
            print_warning(
                ["COULD NOT FIND REPO : {}".format(source),
                 "SKIPPING {}".format(source)]
            )
            continue
        repo.tox = data.get('tox', False)
        repo.parallel_tox = data.get('parallel-tox', True)
        repo.environment = data.get('environment', None)
        repo.tab_name = data.get('tab-name', None)
        get_mps(repo, b)
        if repo.pull_request_count > 0:
            repos.append(repo)
        print(repo)
    collected = [r.name for r in repos]
    print('collected: {}'.format(collected))
    for owner, data in sources['owners'].items():
        print(owner, data)
        repos.extend(get_branches_for_owner(
            lp, collected, owner, data['max-age']))
    return repos


def get_lp_repos(sources, output_directory=None, lp_credentials_store=None):
    '''Return all repos, prs and reviews for the given lp-git source.'''
    # deferred import of launchpadagent until required
    from . import launchpadagent
    cachedir_prefix = os.environ.get('SNAP_USER_COMMON', "/tmp")
    launchpad_cachedir = os.path.join('{}/get_reviews/.launchpadlib'.format(cachedir_prefix))
    lp = launchpadagent.get_launchpad(
        launchpadlib_dir=launchpad_cachedir,
        lp_credentials_store=lp_credentials_store)
    repos = []
    for source, data in sources['repos'].items():
        print(source, data)
        b = lp.git_repositories.getByPath(path=source.replace('lp:', ''))
        try:
            repo = LaunchpadRepo(b, b.web_link, b.display_name)
        except AttributeError:
            print_warning(
                ["COULD NOT FIND REPO : {}".format(source),
                 "SKIPPING {}"].format(source)
            )
            continue
        repo.tox = data.get('tox', False)
        repo.parallel_tox = data.get('parallel-tox', True)
        repo.environment = data.get('environment', None)
        repo.tab_name = data.get('tab-name', None)
        get_mps(repo, b, output_directory)
        if repo.pull_request_count > 0:
            repos.append(repo)
        print(repo)
    return repos


def get_repos(sources, github_username, github_password, github_token):
    if github_token:
        gh = github.Github(github_token)
    elif github_username and github_password:
        gh = github.Github(github_username,
                           github_password)
    else:
        print_warning(
               [("You have configured Github repositories but not supplied "
                "any Github credentials"),
                ("You can either pass these values to review-gator or set "
                 "GITHUB_TOKEN or (GITHUB_USERNAME and GITHUB_PASSWORD) "
                 "environment variables."),
                ("Rendering will proceed but will not include any of your "
                 "Github repositories.")])
        return []

    repos = get_all_repos(gh, sources['repos'])
    return repos


def get_sources(source):
    '''Load the sources file.'''
    data = yaml.load(source.read(), Loader=yaml.SafeLoader)
    return data


def aggregate_reviews(sources, output_directory, github_password, github_token,
                      github_username, tox, lp_credentials_store, tox_jobs):
    try:
        repos = []
        if 'lp-git' in sources:
            repos.extend(get_lp_repos(sources['lp-git'], output_directory,
                                      lp_credentials_store))
        if 'launchpad' in sources:
            # install time dependency on launchpad libs.
            from . import launchpadagent
            repos.extend(get_branches(sources['launchpad'],
                                      lp_credentials_store))
        if 'github' in sources:
            repos.extend(get_repos(sources['github'],
                                   github_username, github_password, github_token))
        # Should we be running tox on any pull requests?
        if tox:
            # Are there any repos with any pull requests requiring a tox run?
            tox_repos = [repo for repo in repos if getattr(repo, 'pull_request_requiring_tox_count', 0) > 0]
            tox_mps = []

            for tox_repo in tox_repos:
                tox_mps.extend(tox_repo.pull_requests_requiring_tox)

            # For all pull requests requiring a tox run set the initial state
            # as running, then render the report as normal.
            Parallel(n_jobs=tox_jobs)(
                delayed(tox_runner.prep_tox_state)(
                    output_directory,
                    tox_mp.web_link.split('/')[-1])
                for tox_mp, _environment in tox_mps
            )

        # Render the report
        render(repos, output_directory, tox)

        if tox:
            tox_repos_to_run_in_parallel = [tox_repo for tox_repo in tox_repos if tox_repo.parallel_tox]
            tox_repos_to_run_without_parallelization = [tox_repo for tox_repo in tox_repos if not tox_repo.parallel_tox]

            tox_mps_to_run_in_parallel = []
            for tox_repo_to_run_in_parallel in tox_repos_to_run_in_parallel:
                tox_mps_to_run_in_parallel.extend(tox_repo_to_run_in_parallel.pull_requests_requiring_tox)

            tox_mps_to_run_without_parallelization = []
            for tox_repo_to_run_without_parallelization in tox_repos_to_run_without_parallelization:
                tox_mps_to_run_without_parallelization.extend(tox_repo_to_run_without_parallelization.pull_requests_requiring_tox)

            # Once report is rendered with initial state then we can start
            # running the tox tests and update state after each run

            # Run tox without parallelization for all tox mps that have
            # parallel-tox == False. This could be due to waiting to avoid
            # race conditions when running tests in parallel. As is the case
            # for projects that use jenkins-job-builder
            print("**** Running tox tests without parallelization. "
                  "Repos with `parallel-tox: false` set... ")
            for tox_mp, environment in tox_mps_to_run_without_parallelization:
                tox_runner.run_tox(
                    tox_mp.source_git_repository.display_name,
                    _format_git_branch_name(tox_mp.source_git_path),
                    output_directory,
                    tox_mp.web_link.split('/')[-1],
                    parallel_tox=False,
                    environment=environment)

            # Run all remaining tox tests that can be run in parallel
            print("**** Running remaining tox tests in parallel... ")
            Parallel(n_jobs=tox_jobs)(
                delayed(tox_runner.run_tox)(
                    tox_mp.source_git_repository.display_name,
                    _format_git_branch_name(tox_mp.source_git_path),
                    output_directory,
                    tox_mp.web_link.split('/')[-1],
                    environment=environment)
                for tox_mp, environment in tox_mps_to_run_in_parallel
            )

        last_poll = format_datetime(localize_datetime(datetime.datetime.utcnow()))
        print("Last run @ {}".format(last_poll))
    except socket.timeout as se:
        print_warning(
            ("Socket.timeout error querying github/launchpad: %s. "
              "We will retry. \n".format(str(se))))
    except TimeoutError as e:
        print_warning(
            ("Socket.timeout error querying github/launchpad: %s. "
              "We will retry. \n".format(str(e))))


@click.command()
@click.option('--config-skeleton', is_flag=True, default=False,
              help='Print example config.')
@click.option('--config', required=True, type=click.File('r'),
              help="Config yaml specifying which repositories/branches to "
                   "query.{}".format(" When using the review-gator snap this"
                                     " config must reside under $HOME."
                                     if os.environ.get('SNAP', None) else ""),
              cls=clicklib.NotRequiredIf,
              mutually_exclusive=['config_skeleton'])
@click.option('--output-directory', envvar='REVIEW_GATOR_OUTPUT_DIRECTORY',
              required=False, type=click.Path(), default=lambda:
              os.environ.get('SNAP_USER_COMMON', "/tmp/review_gator/"),
              help="Output directory. [default: {}]. You can also set "
                   "REVIEW_GATOR_OUTPUT_DIRECTORY as an environment "
                   "variable.{}"
                   .format(os.environ.get('SNAP_USER_COMMON',
                                          "/tmp/review_gator/"),
                           " When using the review-gator snap this config "
                           "must reside under $HOME."
                           if os.environ.get('SNAP', None) else ""))
@click.option('--github-username', envvar='GITHUB_USERNAME', required=False,
              help="Your github username."
                   "You can also set GITHUB_USERNAME as an environment "
                   "variable.", default=None)
@click.option('--github-password', envvar='GITHUB_PASSWORD', required=False,
              help="Your github password."
                   "You can also set GITHUB_PASSWORD as an environment "
                   "variable.", default=None)
@click.option('--github-token', envvar='GITHUB_TOKEN', required=False,
              help="Your github api token. If you provide this then you do not"
                   "need to provide username and password. "
                   "You can also set GITHUB_TOKEN as an environment "
                   "variable.", default=None)
@click.option('--poll', is_flag=True, default=False,
              help='Keep aggregating reviews at a specified interval')
@click.option('--tox', is_flag=True, default=False,
              help='If repo config "tox: true" run tox and show result '
                   'as graphic')
@click.option('--poll-interval', type=int, required=False, default=600,
              help="Interval, in seconds, between each version check "
                   "[default: 600 seconds]")
@click.option('--lp-credentials-store', envvar='LP_CREDENTIALS_STORE',
              required=False,
              help="An optional path to an already configured launchpad "
                   "credentials store.", default=None)
@click.option('--tox-jobs', type=int, required=False, default=-1,
              help="Number of parallelized tox jobs. Default is -1, running "
                   "as many jobs as the processor allows. ")
def main(config_skeleton, config, output_directory,
         github_username, github_password, github_token, poll,
         tox, poll_interval, lp_credentials_store, tox_jobs):
    """Start here."""
    global NOW
    if config_skeleton:
        with open(resource_filename(
                'review_gator', 'config-skeleton.yaml'), 'r') as config_file:
            package_config = yaml.load(config_file)

            output = yaml.dump(package_config, Dumper=yaml.Dumper)
            print("# Sample config.")
            print(output)
            exit(0)

    sources = get_sources(config)
    aggregate_reviews(sources, output_directory, github_password,
                      github_token, github_username, tox,
                      lp_credentials_store, tox_jobs)

    if poll:
        # We do use time.sleep which is blocking so it is best to 'nice'
        # the process to reduce CPU usage. https://linux.die.net/man/1/nice
        os.nice(19)
        while True:
            next_poll = format_datetime(
                    localize_datetime(
                            datetime.datetime.utcnow() +
                            datetime.timedelta(seconds=poll_interval)))
            print("Next run @ {}".format(next_poll))
            time.sleep(poll_interval)  # wait before checking again
            NOW = localize_datetime(datetime.datetime.utcnow())
            aggregate_reviews(sources, output_directory, github_password,
                              github_token, github_username, tox,
                              lp_credentials_store)


if __name__ == '__main__':
    sys.exit(main())
