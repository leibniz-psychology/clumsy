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

"""
Create and delete user accounts on behalf of other daemons.

Users must be local to the system this service is running on.
"""

import secrets, random, functools, re, asyncio, os, time
from contextlib import AsyncExitStack
from collections import namedtuple

import aiohttp
import bonsai
from sanic import Blueprint, response
from sanic.log import logger
from sanic.exceptions import ServerError, Forbidden, NotFound
from unidecode import unidecode

from .nss import getUser
from .kadm import KAdm, KAdmException

client = None
kadm = None
flushsession = None
homedirsession = None
delToken = dict ()
sharedDir = '/storage/public'
homeDir = '/storage/home'

def randomSecret (n):
	alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789'
	return ''.join (secrets.choice (alphabet) for i in range (n))

def socketSession (path):
	conn = aiohttp.UnixConnector (path=path)
	return aiohttp.ClientSession(connector=conn)

async def flushUserCache ():
	"""
	Flush the local user caches
	"""
	# hostname does not matter for unix domain socket?
	try:
		logger.debug (f'flushing caches')
		async with flushsession.delete (f'http://localhost/account') as resp:
			deldata = await resp.json ()
			if deldata['status'] != 'ok':
				raise ServerError ({'status': 'flush_failed', 'nscdflush_status': deldata['status']})
	except aiohttp.ClientError:
		raise ServerError ({'status': 'nscdflushd_connect'})

# revoke ACL while deleting the user
async def revokeAcl (uid, gid):
	proc = await asyncio.create_subprocess_exec ('setfacl', '-R', '-x', f'u:{uid}', '-x', f'g:{gid}', f'{homeDir}', f'{sharedDir}',
                                              stdout=asyncio.subprocess.PIPE,
                                              stderr=asyncio.subprocess.PIPE)
	stdout, stderr = await proc.communicate()

bp = Blueprint('usermgrd')

@bp.listener('before_server_start')
async def setup (app, loop):
	global client, kadm, flushsession, homedirsession

	config = app.config

	client = bonsai.LDAPClient (config.LDAP_SERVER)
	client.set_credentials ("SIMPLE", user=config.LDAP_USER, password=config.LDAP_PASSWORD)

	kadm = KAdm (config.KERBEROS_USER, config.KERBEROS_KEYTAB)

	flushsession = socketSession (config.NSCDFLUSHD_SOCKET)
	homedirsession = socketSession (config.MKHOMEDIRD_SOCKET)

@bp.listener('after_server_stop')
async def teardown (app, loop):
	await flushsession.close ()
	await homedirsession.close ()

def withRollback (func):
	"""
	Rollback operations performed by func if it fails (i.e. Exception is raised)

	func is passed an additional argument rollback and is responsible for
	adding callbacks to it using push_async_callback() or callback().
	"""
	@functools.wraps(func)
	async def wrapped(*args, **kwargs):
		async with AsyncExitStack () as stack:
			ret = await func(*args, **kwargs, rollback=stack)
			stack.pop_all ()
			return ret
	return wrapped

UserInfo = namedtuple ('UserInfo',
		['firstName', 'lastName', 'username', 'orcid', 'authorization',
		'email'],
		defaults=[None]*6)

def possibleUsernames (userdata, minlen=3, maxlen=16):
	"""
	Create UNIX account names based on submitted user data
	"""

	def generate (maxlen):
		prefixes = []
		# preferred username
		if userdata.username:
			prefixes.append (unidecode (userdata.username))
		# otherwise use the actual name
		if userdata.firstName and userdata.lastName:
			prefixes.append ((unidecode (userdata.firstName)[0] + unidecode (userdata.lastName)))
		for postfix in [''] + list (range (1, 10)):
			for prefix in prefixes:
				postfix = str (postfix)
				yield f'{prefix[:maxlen-len (postfix)]}{postfix}'

	r = re.compile (r'[^a-z0-9]')
	for u in generate (maxlen):
		u = r.sub ('', u.lower ())
		# usernames must be reasonably long and cannot start with a digit
		if len (u) >= minlen and u[0].isalpha ():
			yield u

def findUnused (it):
	""" From iterator it find unused user or user id """
	for u in it:
		try:
			res = getUser (u)
		except KeyError:
			return u
	return None

@bp.route ('/', methods=['POST'])
@withRollback
async def addUser (request, rollback):
	config = request.app.config

	form = request.json
	logger.debug (f'creating new user from {form}')
	userdata = UserInfo (**form)

	# make sure the sanitized usernames are >= 3 characters long
	user = findUnused (possibleUsernames (userdata))
	if not user:
		raise ServerError ({'status': 'username'})

	uid = gid = findUnused ([random.randrange (config.MIN_UID, config.MAX_UID) \
			for i in range (100)])
	if not uid:
		raise ServerError ({'status': 'uid'})

	conn = await client.connect (is_async=True)
	o = bonsai.LDAPEntry(config.LDAP_ENTRY_PEOPLE.format (user=user))
	o['objectClass'] = [
			'top',
			'person',
			'organizationalPerson',
			'inetOrgPerson',
			'posixAccount',
			'shadowAccount',
			]
	# LDAP: person
	o['sn'] = userdata.lastName
	o['cn'] = user
	# LDAP: inetOrgPerson
	o['givenName'] = userdata.firstName
	o['mail'] = userdata.email
	# LDAP: posixAccount
	o['uid'] = user
	o['uidNumber'] = uid
	o['gidNumber'] = gid
	o['homeDirectory'] = config.HOME_TEMPLATE.format (user=user)
	o['loginShell'] = '/bin/bash'
	try:
		logger.debug (f'adding user {o} to ldap')
		await conn.add (o)
		# LIFO -> flush cache last
		rollback.push_async_callback (flushUserCache)
		rollback.push_async_callback (o.delete)
	except bonsai.errors.AlreadyExists:
		raise ServerError ({'status': 'user_exists'})

	o = bonsai.LDAPEntry (config.LDAP_ENTRY_GROUP.format (user=user))
	o['objectClass'] = ['top', 'posixGroup']
	o['cn'] = user
	o['gidNumber'] = gid
	o['memberUid'] = user
	try:
		logger.debug (f'adding group {o} to ldap')
		await conn.add (o)
		rollback.push_async_callback (o.delete)
	except bonsai.errors.AlreadyExists:
		raise ServerError ({'status': 'group_exists'})
	conn.close ()

	# flush and sanity check to make sure the user actually exists now and
	# is resolvable in both directions (user->uid; uid->user)
	ok = False
	for i in range (60):
		await flushUserCache ()

		try:
			resUser = getUser (user)
			resUid = getUser (uid)
			if resUser != resUid:
				raise ServerError ({'status': 'user_mismatch'})
			ok = True
			break
		except KeyError:
			logger.debug (f'user {user} not resolvable yet, retrying')
			await asyncio.sleep (1)
	if not ok:
		raise ServerError ({'status': 'user_add_failed'})

	try:
		logger.debug (f'adding kerberos user {user}')
		password = randomSecret (32)
		await kadm.addPrincipal (user, password, expire=config.KERBEROS_EXPIRE)
		rollback.push_async_callback (kadm.deletePrincipal, user)
	except KAdmException:
		raise ServerError ({'status': 'kerberos_failed'})

	# create homedir
	try:
		logger.debug (f'adding homedir for {user}')
		async with homedirsession.post (f'http://localhost/{user}') as resp:
			data = await resp.json ()
			if data['status'] != 'ok':
				raise ServerError ({'status': 'mkhomedir_failed', 'mkhomedird_status': data['status']})
	except aiohttp.ClientError:
		raise ServerError ({'status': 'mkhomedird_connect'})

	return response.json ({'status': 'ok', 'user': user, 'password': password, 'uid': uid, 'gid': uid}, status=201)

@bp.route ('/<user>', methods=['DELETE'])
async def deleteUser (request, user):
	"""
	Delete user from the cluster.

	Including: LDAP user and group, kerberos principal, home directory, guix
	profile directory
	"""

	config = request.app.config
	delFile = None
	delUser = None
	owner = None
	start = 0.0
	uid = 0
	gid = 0
	now = time.time ()
	# in seconds
	tokenTimeout = 60

	try:
		res = getUser (user)
		uid = res['uid']
		gid = res['gid']
		delUser = res['name']
	except KeyError:
		raise NotFound ({'status': 'user_not_found'})

	if not (config.MIN_UID <= uid < config.MAX_UID):
		raise Forbidden ({'status': 'unauthorized'})

	if user not in delToken:
		while True:
			newToken = randomSecret(32)
			delFile = os.path.join(res['homedir'], 'confirm_deletion' + '_' + newToken)
			delToken[delUser] = (delFile, now)
			if not os.path.exists (delFile):
				return response.json ({'status': 'again', 'token': delFile})

	delFile, start = delToken.pop (user)

	# check whether the file exists, belongs to the user who requested deletion, both the request and the token is recent
	try:
		ownerUid = os.stat(delFile).st_uid
	except FileNotFoundError:
		raise NotFound ({'status': 'no_proof'})

	if ownerUid == uid and (now - start) <= tokenTimeout and (now - os.path.getctime(delFile)) <= tokenTimeout:
		# disallow logging in by deleting principal
		try:
			await kadm.getPrincipal (user)
			# XXX: race-condition
			await kadm.deletePrincipal (user)
		except KeyError:
			logger.warning (f'kerberos principal for {user} already gone')
		except KAdmException:
			raise ServerError ({'status': 'kerberos_failed'})

		# mark homedir for deletion
		async with homedirsession.delete (f'http://localhost/{user}') as resp:
			deldata = await resp.json ()
			if deldata['status'] != 'again':
				raise ServerError ({'status': 'mkhomedird_token', 'mkhomedird_status': deldata['status']})

		conn = await client.connect (is_async=True)
		await conn.delete (config.LDAP_ENTRY_PEOPLE.format (user=user))
		await conn.delete (config.LDAP_ENTRY_GROUP.format (user=user))
		conn.close ()

		await flushUserCache ()

		# finally delete homedir
		async with homedirsession.delete (f'http://localhost/{user}', params={'token': deldata['token']}) as resp:
			deldata = await resp.json ()
			if deldata['status'] != 'ok':
				raise ServerError ({'status': 'mkhomedir_delete', 'mkhomedird_status': deldata['status']})

		asyncio.ensure_future (revokeAcl (uid, gid))
		return response.json ({'status': 'ok'})
	else:
		# user did not prove he is allowed to do this
		raise Forbidden ({'status': 'invalid_proof'})

