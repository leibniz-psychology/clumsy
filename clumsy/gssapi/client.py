# Copyright (c) 2016 University of Oxford.
# Copyright (c) 2022 Leibniz Institute for Psychology
# 
# All rights reserved.
# 
# Redistribution and use in source and binary forms are permitted
# provided that the above copyright notice and this paragraph are
# duplicated in all such forms and that any documentation,
# advertising materials, and other materials related to such
# distribution and use acknowledge that the software was developed by
# the University of Oxford. The name of the University of Oxford may
# not be used to endorse or promote products derived from this
# software without specific prior written permission.
# 
# THIS SOFTWARE IS PROVIDED "AS IS" AND WITHOUT ANY EXPRESS OR
# IMPLIED WARRANTIES, INCLUDING, WITHOUT LIMITATION, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.

from base64 import b64decode, b64encode
from http.client import UNAUTHORIZED
import logging

import aiohttp
import gssapi
import www_authenticate

logger = logging.getLogger (__name__)

class NegotiateMixin(object):
	def __init__(self, *,
				 negotiate_client_name=None,
				 negotiate_service_name=None,
				 negotiate_service='HTTP',
				 **kwargs):
		self.negotiate_client_name = negotiate_client_name
		self.negotiate_service_name = negotiate_service_name
		self.negotiate_service = negotiate_service
		super().__init__(**kwargs)

	def get_hostname(self, response):
		assert isinstance(response, aiohttp.ClientResponse)
		return response.url.host

	def get_context(self, host):
		if self.negotiate_service_name:
			service_name = gssapi.Name(self.negotiate_service_name)
		else:
			service_name = gssapi.Name('{0}@{1}'.format(self.negotiate_service, host),
					gssapi.NameType.hostbased_service)

		logger.debug (f'Creating context with service {service_name} for host {host}')
		if self.negotiate_client_name:
			creds = gssapi.Credentials(name=gssapi.Name(self.negotiate_client_name),
									   usage='initiate')
		else:
			creds = None
		return gssapi.SecurityContext(name=service_name,
									  creds=creds)

	def get_token (self, response):
		header = response.headers.get ('www-authenticate')
		if header:
			challenges = www_authenticate.parse(header)
			token = challenges.get ('negotiate')
			logger.debug (f'got token {token}')
			return (True, token)
		return (False, None)

	def negotiate_step(self, ctx, in_token=None):
		if in_token:
			in_token = b64decode(in_token)
		out_token = ctx.step(in_token)
		if out_token:
			out_token = b64encode(out_token).decode('utf-8')
		return out_token

	async def _request(self, method, url, *, headers=None, **kwargs):
		logger.debug (f'Overridingi _request for {method} {url}')
		headers = headers or {}
		response = await super()._request(method, url, headers=headers, **kwargs)
		logger.debug (f'got response {response}')
		isNegotiate, token = self.get_token (response)
		if response.status == UNAUTHORIZED and isNegotiate:
			logger.debug ('unauthorized, trying negotiation')
			host = self.get_hostname(response)
			ctx = self.get_context(host)
			out_token = self.negotiate_step(ctx)
			for i in range (10):
				response.close()
				if out_token:
					headers['Authorization'] = 'Negotiate ' + out_token
					response = await super()._request(method, url, headers=headers, **kwargs)
				isNegotiate, token = self.get_token  (response)
				if isNegotiate:
					out_token = self.negotiate_step(ctx, token)
				if ctx.complete or i >= 9 or response.status != UNAUTHORIZED:
					break
		return response

class NegotiateClientSession(NegotiateMixin, aiohttp.ClientSession):
	pass

