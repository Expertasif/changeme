import base64
from changeme.report import Report
import jinja2
from requests import session
from .scanner import Scanner
import re
from tempfile import NamedTemporaryFile
from time import sleep
try:
    # Python 3
    from urllib.parse import urlencode, urlparse
except ImportError:
    # Python 2
    from urllib import urlencode
    from urlparse import urlparse


class HTTPGetScanner(Scanner):

    def __init__(self, cred, target, username, password, config, cookies):
        super(HTTPGetScanner, self).__init__(cred, target, config, username, password)
        self.cred = cred
        self.config = config
        self.cookies = cookies
        self.headers = dict()
        self.request = session()
        self.response = None

        headers = self.cred['auth'].get('headers', dict())
        if headers:
            for h in headers:
                self.headers.update(h)
        self.headers.update(self.config.useragent)

        # make the cred have only one u:p combo
        self.cred['auth']['credentials'] = [{'username': self.username, 'password': self.password}]

    def __reduce__(self):
        return (self.__class__, (self.cred, self.target, self.username, self.password, self.config, self.cookies))

    def scan(self):
        try:
            self._make_request()
        except Exception as e:
            self.logger.error('Failed to connect to %s' % self.target)
            self.logger.debug('Exception: %s: %s' % (type(e).__name__, e.__str__().replace('\n', '|')))
            return None

        if self.response.status_code == 429:
            self.warn('Status 429 received. Sleeping for %d seconds and trying again' % self.config.delay)
            sleep(self.config.delay)
            try:
                self._make_request()
            except Exception as e:
                self.logger.error('Failed to connect to %s' % self.target)

        return self.check_success()

    def check_success(self):
        match = False
        success = self.cred['auth']['success']

        if self.cred['auth'].get('base64', None):
            self.username = base64.b64decode(self.cred.username)
            self.password = base64.b64decode(self.cred.password)

        if success.get('status') == self.response.status_code:
            if success.get('body'):
                for string in success.get('body'):
                    if re.search(string, self.response.text, re.IGNORECASE):
                        match = True
                        break
            else:
                match = True

        if match:
            self.logger.critical('[+] Found %s default cred %s:%s at %s' %
                                 (self.cred['name'], self.username, self.password, self.target))

            self._screenshot()
            return {'name': self.cred['name'],
                    'username': self.username,
                    'password': self.password,
                    'target': self.target}
        else:
            self.logger.info('Invalid %s default cred %s:%s at %s' %
                             (self.cred['name'], self.username, self.password, self.target))
            return False

    def _check_fingerprint(self):
        self.logger.debug("_check_fingerprint")
        self.request = session()
        self.response = self.request.get(self.target,
                                         timeout=self.config.timeout,
                                         verify=False,
                                         proxies=self.config.proxy,
                                         cookies=self.fingerprint.cookies,
                                         headers=self.fingerprint.headers)
        self.logger.debug('_check_fingerprint', '%s - %i' % (self.target, self.response.status_code))
        return self.fingerprint.match(self.response)

    def _make_request(self):
        self.logger.debug("_make_request")
        data = self.render_creds(self.cred)
        qs = urlencode(data)
        url = "%s?%s" % (self.target, qs)
        self.logger.debug("url: %s" % url)
        self.response = self.request.get(self.target,
                                         verify=False,
                                         proxies=self.config.proxy,
                                         timeout=self.config.timeout,
                                         headers=self.headers,
                                         cookies=self.cookies)

    def render_creds(self, candidate, csrf=None):
        """
            Return a list of dicts with post/get data and creds.

            The list of dicts have a data element and a username and password
            associated with the data. The data will either be a dict if its a
            regular GET or POST and a string if its a raw POST.
        """
        b64 = candidate['auth'].get('base64', None)
        type = candidate['auth'].get('type')
        config = None
        if type == 'post':
            config = candidate['auth'].get('post', None)
        if type == 'get':
            config = candidate['auth'].get('get', None)

        if not type == 'raw_post':
            data = self._get_parameter_dict(candidate['auth'])

            if csrf:
                csrf_field = candidate['auth']['csrf']
                data[csrf_field] = csrf

            for cred in candidate['auth']['credentials']:
                cred_data = {}
                username = ""
                password = ""
                if b64:
                    username = base64.b64encode(cred['username'])
                    password = base64.b64encode(cred['password'])
                else:
                    username = cred['username']
                    password = cred['password']

                cred_data[config['username']] = username
                cred_data[config['password']] = password

                data_to_send = dict(list(data.items()) + list(cred_data.items()))
                return data_to_send
        else:  # raw post
            return None

    def _get_parameter_dict(self, auth):
        params = dict()
        data = auth.get('post', auth.get('get', None))
        for k in list(data.keys()):
            if k not in ('username', 'password', 'url'):
                params[k] = data[k]

        return params

    @staticmethod
    def get_base_url(req):
        parsed = urlparse(req)
        url = "%s://%s" % (parsed[0], parsed[1])
        return url

    def _screenshot(self):
        template_loader = jinja2.FileSystemLoader(searchpath=Report.get_template_path())
        template_env = jinja2.Environment(loader=template_loader)
        capture_template = template_env.get_template('offline.js.j2')
        with NamedTemporaryFile(delete=False) as cf:
            html = self.response.text.replace("'", "\'").replace('\n', '').replace('\r', '')
            capturejs = capture_template.render({'html': html,
                                                 'fname': 'foo.png'})
            print capturejs
            #cf.write(capturejs)
