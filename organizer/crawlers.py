import sys
import time

from botocore.exceptions import ClientError

from organizer import utils, orgs


DEFAULT_REGION = 'us-east-1'


class Crawler(object):

    all_regions = utils.all_regions()

    def __init__(self, org, **kwargs):
        """
        kwargs:
        :access_role: string
        :accounts: string, list of string, or list of OrgAccount
        :regions: string, or list of string
        """
        self.org = org
        self.access_role = kwargs.get('access_role') or org.access_role
        self.accounts = kwargs.get('accounts') or org.accounts
        self.validate_accounts()
        self.regions = kwargs.get('regions') or self.all_regions
        self.validate_regions()
        self.requests = []
        self.exc_info = None
        self.error = None

    def validate_accounts(self):
        if isinstance(self.accounts, str) or isinstance(self.accounts, orgs.OrgAccount):
            self.accounts = [self.accounts]
        elif not isinstance(self.accounts, list):
            raise ValueError(
                'keyword argument "accounts" must be str, list or orgs.OrgAccount'
            )
        self.accounts = [self.org.get_account(a) for a in self.accounts]

    def validate_regions(self):
        if self.regions == 'GLOBAL':
            self.regions = [DEFAULT_REGION]
        else:
            if isinstance(self.regions, str):
                self.regions = [self.regions]
            elif not isinstance(self.regions, list):
                raise ValueError('keyword argument "regions" must be list or str')
            no_such_regions = [r for r in self.regions if r not in self.all_regions]
            if no_such_regions:
                raise ValueError('Invalid regions: {}'.format(', '.join(no_such_regions)))

    def get_regions(self):
        return self.regions

    def update_regions(self, regions):
        self.regions = regions
        self.validate_regions()

    def load_account_credentials(self):
        def get_credentials_for_account(account, crawler):
            try:
                account.load_credentials(crawler.access_role)
            except ClientError as e:
                crawler.error = 'cannot assume role {} in account {}: {}'.format(
                    crawler.access_role,
                    account.name,
                    e.response['Error']['Code']
                )
            except Exception:
                crawler.exc_info = sys.exc_info()
        utils.queue_threads(
            self.accounts,
            get_credentials_for_account,
            func_args=(self,),
            thread_count=len(self.accounts)
        )
        if self.error:
            sys.exit(self.error)
        if self.exc_info:
            raise self.exc_info[1].with_traceback(self.exc_info[2])

    # ISSUES:
    # rename CrawlerRequest to CrawlerExecution
    # likewise the Crawler.request attr to Crawler.execution
    #
    # add exception handling as with load_account_credentials
    #
    # forgo use of *args, just allow **kwargs
    #
    def execute(self, payload, *args, **kwargs):

        def run_payload_in_account(account_region_map, request, *args):
            region = account_region_map['region']
            account = account_region_map['account']
            response = CrawlerResponse(region, account)
            response.timer.start()
            try:
                response.payload_output = request.payload(region, account, *args)
            except Exception as e:
                response.exc_info = sys.exc_info()
                request.errors = True
            response.timer.stop()
            request.responses.append(response)

        accounts_and_regions = []
        for region in self.regions:
            for account in self.accounts:
                accounts_and_regions.append(dict(account=account, region=region))
        thread_count = kwargs.get('thread_count', len(self.accounts))
        request = CrawlerRequest(payload)
        request.timer.start()
        utils.queue_threads(
            accounts_and_regions,
            run_payload_in_account,
            func_args=(request, *args),
            thread_count=thread_count,
        )
        request.timer.stop()
        if request.errors:
            request.handle_errors()
        self.requests.append(request)
        return request

    def get_request(self, name):
        return next((r for r in self.requests if r.name == name), None)


class CrawlerTimer(object):

    def __init__(self):
        self.start_time = None
        self.end_time = None
        self.elapsed_time = None

    def start(self):
        self.start_time = time.perf_counter()

    def stop(self):
        if self.start_time:
            self.end_time = time.perf_counter()
            self.elapsed_time = self.end_time - self.start_time

    def dump(self):
        return dict(
            start_time=self.start_time,
            end_time=self.end_time,
            elapsed_time=self.elapsed_time,
        )


class CrawlerRequest(object):

    def __init__(self, payload):
        self.payload = payload
        self.name = payload.__name__
        self.responses = []
        self.errors = None
        self.timer = CrawlerTimer()

    def dump(self):
        return dict(
            payload=self.payload.__repr__(),
            name=self.name,
            responses=[r.dump() for r in self.responses],
            statistics=self.timer.dump()
        )

    def handle_errors(self):
        errors = [response for response in self.responses if response.exc_info]
        exc_info = errors.pop().exc_info
        errmsg = (
            'OrgCrawler.execute encountered {} errors while running "{}". '
            'Example:\n'.format(
                len(errors),
                self.name,
            )
        )
        print(errmsg, file=sys.stderr)
        sys.excepthook(*exc_info)
        sys.exit()


class CrawlerResponse(object):

    def __init__(self, region, account):
        self.region = region
        self.account = account
        self.payload_output = None
        self.timer = CrawlerTimer()
        self.exc_info = None

    def dump(self):
        return dict(
            region=self.region,
            account=self.account.dump(),
            payload_output=self.payload_output,
            statistics=self.timer.dump()
        )
