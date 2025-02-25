# -*- coding: utf-8 -*-

import json
import functools
import logging

import requests
from requests.exceptions import RequestException
from urllib.parse import urlencode, quote

from odata import version
from .exceptions import ODataError, ODataConnectionError


def catch_requests_errors(fn):
    @functools.wraps(fn)
    def inner(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except RequestException as e:
            raise ODataConnectionError(str(e))
    return inner


class ODataConnection(object):
    base_headers = {
        'Accept': 'application/json',
        'OData-Version': '4.0',
        'User-Agent': 'python-odata {0}'.format(version),
    }
    timeout = 90

    def __init__(self, session=None, auth=None, extra_headers: dict = None):
        if session is None:
            self.session = requests.Session()
        else:
            self.session = session
        self.auth = auth
        self.log = logging.getLogger('odata.connection')

        self.extra_headers = extra_headers

        if extra_headers:
            self.base_headers.update(extra_headers)

    def _apply_options(self, kwargs):
        kwargs['timeout'] = self.timeout
        if "params" in kwargs and kwargs["params"]:
            kwargs["params"] = urlencode(kwargs["params"], quote_via=quote)

        if self.auth is not None:
            kwargs['auth'] = self.auth

    @catch_requests_errors
    def _do_get(self, *args, **kwargs):
        self._apply_options(kwargs)
        return self.session.get(*args, **kwargs)

    @catch_requests_errors
    def _do_post(self, *args, **kwargs):
        self._apply_options(kwargs)
        return self.session.post(*args, **kwargs)

    @catch_requests_errors
    def _do_patch(self, *args, **kwargs):
        self._apply_options(kwargs)
        return self.session.patch(*args, **kwargs)

    @catch_requests_errors
    def _do_delete(self, *args, **kwargs):
        self._apply_options(kwargs)
        return self.session.delete(*args, **kwargs)

    def _handle_odata_error(self, response):
        try:
            response.raise_for_status()
        except:
            status_code = 'HTTP {0}'.format(response.status_code)
            code = 'None'
            message = 'Server did not supply any error messages'
            detailed_message = 'None'
            response_ct = response.headers.get('content-type', '')

            if 'application/json' in response_ct:
                errordata = response.json()

                if 'error' in errordata:
                    odata_error = errordata.get('error')

                    code = odata_error.get('code', None) or code
                    message = odata_error.get('message', None) or message
                    if 'innererror' in odata_error:
                        ie = odata_error['innererror']
                        detailed_message = ie.get('message', None) or detailed_message
                    elif (
                        "details" in odata_error
                        and isinstance(odata_error["details"], list)
                        and len(odata_error["details"]) > 0
                    ):
                        details = odata_error["details"][0]
                        detail_code = details.get("code", "")
                        detail_message = details.get("message", detailed_message)
                        detailed_message = (
                            f"({detail_code}): {detail_message}"
                            if detail_code
                            else detail_message
                        )

            elif "application/problem+json" in response_ct:
                errordata = response.json()
                if "exception" in errordata:
                    odata_exception = errordata.get("exception")
                    code = errordata.get("type", None) or code
                    code = errordata.get("errorId", None) or code
                    detailed_message = errordata.get("detail", None) or detailed_message
                    message = odata_exception.get("message", None) or message

                    inner = ["innerexception", "innerException"]
                    for candidate in inner:
                        if candidate in odata_exception:
                            ie = odata_exception[candidate]
                            detailed_message = ie.get("message", None) or detailed_message
                else:
                    detailed_message = response.headers.get('WWW-Authenticate', detailed_message)
            else:
                detailed_message = response.text

            msg = ' | '.join([str(status_code), str(code), str(message), str(detailed_message)])
            err = ODataError(msg)
            err.status_code = status_code
            err.code = code
            err.message = message
            err.detailed_message = detailed_message
            raise err

    def execute_get(self, url, params=None, allow_plain_response=False, extra_headers=None):
        headers = {}
        headers.update(self.base_headers)

        if extra_headers:
            headers.update(extra_headers)

        self.log.info(u'GET {0}'.format(url))
        if params:
            self.log.info(u'Query: {0}'.format(params))

        response = self._do_get(url, params=params, headers=headers)
        self._handle_odata_error(response)
        response_ct = response.headers.get('content-type', '')
        if response.status_code == requests.codes.no_content:
            return
        if 'application/json' in response_ct:
            data = response.json()
            return data
        elif "text/plain" in response_ct and allow_plain_response:
            return response.text
        else:
            msg = u'Unsupported response Content-Type: {0}'.format(response_ct)
            raise ODataError(msg)

    def execute_post(self, url, data, raw: bool = False, params=None, extra_headers=None):
        headers = {
            'Content-Type': 'application/json',
        }
        headers.update(self.base_headers)

        if extra_headers:
            headers.update(extra_headers)

        if not raw:
            data = json.dumps(data)

        self.log.info(u'POST {0}'.format(url))
        self.log.info(u'Payload: {0}'.format(data))

        response = self._do_post(url, data=data, headers=headers, params=params)
        self._handle_odata_error(response)
        response_ct = response.headers.get('content-type', '')
        if response.status_code == requests.codes.no_content:
            return
        if 'application/json' in response_ct:
            return response.json()
        # no exceptions here, POSTing to Actions may not return data

    def execute_patch(self, url, data, extra_headers=None):
        headers = {
            'Content-Type': 'application/json',
        }
        headers.update(self.base_headers)

        if extra_headers:
            headers.update(extra_headers)

        data = json.dumps(data)

        self.log.info(u'PATCH {0}'.format(url))
        self.log.info(u'Payload: {0}'.format(data))

        response = self._do_patch(url, data=data, headers=headers)
        self._handle_odata_error(response)
        response_ct = response.headers.get('content-type', '')
        if response.status_code == requests.codes.no_content:
            return
        if 'application/json' in response_ct:
            return response.json()
        # no exceptions here, PATCHing to Actions may not return data

    def execute_delete(self, url, extra_headers=None):
        headers = {}
        headers.update(self.base_headers)

        if extra_headers:
            headers.update(extra_headers)

        self.log.info(u'DELETE {0}'.format(url))

        response = self._do_delete(url, headers=headers)
        self._handle_odata_error(response)
