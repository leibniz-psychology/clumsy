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

import asyncio, sys, argparse, json, os, logging, socket
from .gssapi.client import NegotiateClientSession

import aiohttp

async def handleUser (args):
	conn = aiohttp.UnixConnector (path=args.socket)
	async with NegotiateClientSession (negotiate_client_name=args.clientPrincipal,
			negotiate_service_name=args.serverPrincipal,
			negotiate_service='usermgrd',
			connector=conn) as usermgrd:
		if args.action == 'create':
			form = None
			if args.name:
				form = {'firstName': 'abc', 'lastName': 'abc', 'username': args.name,
						'orcid': 'abc', 'authorization': 'abc', 'email': 'abc'}
			async with usermgrd.post (f'http://{args.host}/', json=form or json.load (sys.stdin)) as resp:
				data = await resp.json ()
				json.dump (data, sys.stdout)
		elif args.action == 'delete':
			async with usermgrd.delete (f'http://{args.host}/') as resp:
				data = await resp.json ()
				json.dump (data, sys.stdout)

def main ():
	logging.basicConfig (level=logging.INFO)

	parser = argparse.ArgumentParser()
	parser.add_argument('--socket', default='/run/usermgrd.socket', help='Connect to socket')
	parser.add_argument('--client-principal', dest='clientPrincipal', help='Kerberos client principal to use')
	parser.add_argument('--server-principal', dest='serverPrincipal', help='Kerberos server principal to use')
	parser.add_argument('--host', default=socket.gethostname(), help='')
	parser.add_argument('--keytab', help='Custom keytab for authentication')
	parser.add_argument('--krb5-config', dest='krb5Config', help='Custom Kerberos configuration file')
	parser.add_argument('--debug', action='store_true', help='Turn on debugging')

	subparsers = parser.add_subparsers(help='sub-command help')

	parser_user = subparsers.add_parser('user', aliases=['u'], help='User management')
	parser_user.add_argument('action', choices=('create', 'delete'), help='User management mode')
	parser_user.add_argument('--name', help='Name for new user')
	parser_user.set_defaults(func=handleUser)

	args = parser.parse_args ()
	if args.debug:
		logging.getLogger().setLevel (logging.DEBUG)
	if args.krb5Config:
		os.environ['KRB5_CONFIG'] = args.krb5Config
	if args.keytab:
		logging.debug (f'Using keytab in {args.keytab}')
		# See https://web.mit.edu/kerberos/krb5-1.12/doc/admin/env_variables.html
		os.environ['KRB5_CLIENT_KTNAME'] = args.keytab
	return asyncio.run (args.func (args))

