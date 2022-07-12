# Copyright 2019–2020 Leibniz Institute for Psychology
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# guix shell curl -- curl -v --negotiate --service-name 'usermgrd' -u : raipur:8000/foo

from collections import defaultdict
from base64 import b64encode, b64decode
from functools import wraps

from sanic.response import text
from sanic.exceptions import Unauthorized
from sanic.log import logger
import www_authenticate, gssapi
from gssapi.raw.misc import GSSError

# export KRB5_KTNAME=usermgrd.keytab

def authorized(serverNameConfigKey):
	""" SPNEGO-based GSSAPI authorization, see https://datatracker.ietf.org/doc/html/rfc4559 """
	def makeContext (servername):
		servername = gssapi.Name(servername)
		logger.debug (f'Creating gssapi context for {servername}')
		creds = gssapi.Credentials (usage='accept', name=servername)
		return gssapi.SecurityContext (usage='accept', creds=creds)

	def decorator(f):
		@wraps(f)
		async def decorated_function(request, *args, **kwargs):
			servername = getattr (request.app.config, serverNameConfigKey)

			connctx = request.conn_info.ctx
			if not hasattr (connctx, 'gss'):
				connctx.gss = makeContext (servername)
			gss = connctx.gss

			nextChallenge = None
			authHeader = request.headers.get ('Authorization')
			if authHeader:
				logger.debug (f'Got authorization header {authHeader}')
				parsed = www_authenticate.parse (authHeader)
				challenge = parsed.get ('Negotiate')
				if challenge:
					logger.debug (f'Got challenge {challenge}')
					try:
						nextChallenge = gss.step (b64decode (challenge))
					except GSSError as e:
						# Destroy the context and try again
						connctx.gss = makeContext (servername)
						gss = connctx.gss

			headers = dict ()
			if nextChallenge is not None:
				headers['WWW-Authenticate'] = f'Negotiate {b64encode (nextChallenge).decode("ascii")}'
			else:
				headers['WWW-Authenticate'] = 'Negotiate'

			if gss.complete:
				logger.debug (f'Client accepted, go on!')
				response = await f (request, *args, **kwargs, user=str (gss.initiator_name))
				response.headers.update (headers)
				return response
			else:
				logger.debug (f'Client needs to send credentials, responding with {headers}')
				return text ('Credentials required', status=401, headers=headers)
		return decorated_function
	return decorator

