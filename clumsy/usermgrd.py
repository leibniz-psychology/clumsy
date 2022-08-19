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

import secrets, random, functools, re, asyncio, grp
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
from .gssapi.server import authorized

ldapclient = None
kadm = None
flushsession = None
homedirsession = None
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
	global ldapclient, kadm, flushsession, homedirsession

	config = app.config

	ldapclient = bonsai.LDAPClient (config.LDAP_SERVER)
	ldapclient.set_credentials ("SIMPLE", user=config.LDAP_USER, password=config.LDAP_PASSWORD)

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

def numberedVariants (prefixes, maxlen, n):
	for postfix in [''] + list (range (1, n)):
		for prefix in prefixes:
			postfix = str (postfix)
			yield f'{prefix[:maxlen-len (postfix)]}{postfix}'

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
		yield from numberedVariants (prefixes, maxlen, 100)

	r = re.compile (r'[^a-z0-9]')
	for u in generate (maxlen):
		u = r.sub ('', u.lower ())
		# usernames must be reasonably long and cannot start with a digit
		if len (u) >= minlen and u[0].isalpha ():
			yield u

def findUnusedUser (it):
	""" From iterator it find unused user or user id """
	for u in it:
		try:
			res = getUser (u)
		except KeyError:
			return u
	return None

@bp.route ('/user', methods=['POST'])
# @authorize is not async, but I’m not aware of any async gssapi module -.-
@authorized('KERBEROS_USER')
@withRollback
async def addUser (request, rollback, user):
	config = request.app.config

	if user.split ('@', 1)[0] != config.AUTHORIZATION_CREATE:
		raise Forbidden ()

	form = request.json
	logger.debug (f'creating new user from {form}')
	userdata = UserInfo (**form)

	# make sure the sanitized usernames are >= 3 characters long
	user = findUnusedUser (possibleUsernames (userdata))
	if not user:
		raise ServerError ({'status': 'username'})

	uid = gid = findUnusedUser ([random.randrange (config.MIN_UID, config.MAX_UID) \
			for i in range (100)])
	if not uid:
		raise ServerError ({'status': 'uid'})

	conn = await ldapclient.connect (is_async=True)
	o = bonsai.LDAPEntry(f'uid={user},{config.LDAP_BASE_PEOPLE}')
	o['objectClass'] = [
			'top',
			'person',
			'organizationalPerson',
			'inetOrgPerson',
			'posixAccount',
			'shadowAccount',
			] + config.LDAP_EXTRA_CLASSES
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

	o = bonsai.LDAPEntry (f'cn={user},{config.LDAP_BASE_GROUP}')
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

	logger.info (f'Created user {user} for '
			f'{userdata.firstName} {userdata.lastName} ({userdata.email}) '
			f'with UID/GID {uid}')
	return response.json ({'status': 'ok', 'user': user, 'password': password, 'uid': uid, 'gid': uid}, status=201)

@bp.route ('/user', methods=['DELETE'])
@authorized('KERBEROS_USER')
async def deleteUser (request, user):
	"""
	Delete user from the cluster.

	Including: LDAP user and group, kerberos principal, home directory, guix
	profile directory
	"""

	config = request.app.config

	try:
		user = user.split ('@', 1)[0]
		res = getUser (user)
		uid = res['uid']
		gid = res['gid']
		logger.info (f'Got request to delete {user} {uid}/{gid}')
	except KeyError:
		raise NotFound ({'status': 'user_not_found'})

	if not (config.MIN_UID <= uid < config.MAX_UID):
		raise Forbidden ({'status': 'unauthorized'})

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

	conn = await ldapclient.connect (is_async=True)
	try:
		await conn.delete (f'uid={user},{config.LDAP_BASE_PEOPLE}')
	except bonsai.errors.NoSuchObjectError:
		logger.warning (f'LDAP user {user} already gone')
	try:
		await conn.delete (f'cn={user},{config.LDAP_BASE_GROUP}')
	except bonsai.errors.NoSuchObjectError:
		logger.warning (f'LDAP group {user} already gone')
	# Find all secondary groups user is member in and delete membership.
	results = await conn.search (config.LDAP_BASE_GROUP,
			bonsai.LDAPSearchScope.SUBTREE,
			f'(&(objectClass=posixGroup)(memberUid={user}))')
	for g in results:
		g['memberUid'].remove (user)
		await g.modify ()
	await garbageCollectGroups (config, conn)
	conn.close ()

	await flushUserCache ()

	# finally delete homedir
	async with homedirsession.delete (f'http://localhost/{user}', params={'token': deldata['token']}) as resp:
		deldata = await resp.json ()
		if deldata['status'] != 'ok':
			raise ServerError ({'status': 'mkhomedir_delete', 'mkhomedird_status': deldata['status']})

	asyncio.ensure_future (revokeAcl (uid, gid))

	logger.info (f'Deleted user {user}')
	return response.json ({'status': 'ok'})

def findUnusedGroup (it):
	""" From iterator it find unused group or group id """
	for u in it:
		try:
			if isinstance (u, str):
				res = grp.getgrnam (u)
			elif isinstance (u, int):
				res = grp.getgrgid (u)
		except KeyError:
			return u
	return None

def possibleGroupnames (owner, name, minlen=3, maxlen=32):
	"""
	Create valid UNIX group names based on submitted user data
	"""

	r = re.compile (r'[^a-z0-9-]')
	owner = r.sub ('', owner.lower ())
	name = r.sub ('', name.lower ())
	for g in numberedVariants ([f'{owner}-{name}'], maxlen, 100):
		# group names must be reasonably long and cannot start with a digit
		if len (g) >= minlen and g[0].isalpha ():
			yield g

def ensureUser (username : str, config, error : str ='user_not_found'):
	""" Ensure user exists and we are allowed to operate on it """
	try:
		user = getUser (username)
		if not (config.MIN_UID <= user['uid'] < config.MAX_UID):
			raise Forbidden ({'status': 'unauthorized'})
		return user
	except KeyError:
		raise NotFound ({'status': error})

@bp.route ('/group/<newGroupName>', methods=['POST'])
@authorized('KERBEROS_USER')
@withRollback
async def addGroup (request, rollback, newGroupName, user):
	""" Create a new group """

	config = request.app.config

	owner = ensureUser (user, config)

	group = findUnusedGroup (possibleGroupnames (owner['name'], newGroupName))
	if not group:
		raise ServerError ({'status': 'groupname'})

	gid = gid = findUnusedGroup ([random.randrange (config.MIN_GID, config.MAX_GID) \
			for i in range (100)])
	if not gid:
		raise ServerError ({'status': 'gid'})

	conn = await ldapclient.connect (is_async=True)
	o = bonsai.LDAPEntry(f'cn={group},{config.LDAP_BASE_GROUP}')
	o['objectClass'] = [
			'top',
			'posixGroup',
			]
	o['cn'] = group
	o['gidNumber'] = gid
	o['memberUid'] = owner['name']
	try:
		logger.debug (f'adding group {o} to ldap')
		await conn.add (o)
		# LIFO -> flush cache last
		rollback.push_async_callback (flushUserCache)
		rollback.push_async_callback (o.delete)
	except bonsai.errors.AlreadyExists:
		raise ServerError ({'status': 'group_exists'})

	# flush and sanity check to make sure the group actually exists now and
	# is resolvable in both directions (gid→name, name→gid)
	ok = False
	for i in range (60):
		await flushUserCache ()

		try:
			resNam = grp.getgrnam (group)
			resGid = grp.getgrgid (gid)
			if resNam != resGid:
				raise ServerError ({'status': 'group_mismatch'})
			ok = True
			break
		except KeyError:
			logger.debug (f'group {group}/{gid} not resolvable yet, retrying')
			await asyncio.sleep (1)
	if not ok:
		raise ServerError ({'status': 'resolve_timeout'})

	logger.info (f'Created group {group} for {owner["name"]} with GID {gid}')
	return response.json ({
			'status': 'ok',
			'group': group,
			'gid': gid,
			'members': [getUser (u)['name'] for u in resGid.gr_mem],
			}, status=201)

def ensureGroup (name, config):
	""" Ensure group exists and we are allowed to operate on it """
	try:
		group = grp.getgrnam (name)
		if not (config.MIN_GID <= group.gr_gid < config.MAX_GID):
			raise Forbidden ({'status': 'unauthorized'})
		return group
	except KeyError:
		raise NotFound ({'status': 'nonexistent'})

def ensureGroupMember (group : grp.struct_group, user : dict):
	""" Ensure username is part of group. """

	if user['name'] not in group.gr_mem:
		raise Forbidden ({'status': 'not_a_member'})

async def getLdapGroup (conn, config, name : str):
	results = await conn.search (f'cn={name},{config.LDAP_BASE_GROUP}',
			bonsai.LDAPSearchScope.BASE)
	if len (results) != 1:
		# should never happen
		raise Forbidden (dict (status='inconsistent'))
	results, = results
	return results

@bp.route ('/group/<modgroup>/<moduser>', methods=['POST'])
@authorized('KERBEROS_USER')
async def addUserToGroup (request, user, modgroup, moduser):
	"""
	Add user to existing group.
	"""

	config = request.app.config

	modgroup = ensureGroup (modgroup, config)
	gid = modgroup.gr_gid

	user = ensureUser (user, config, 'you_do_not_exist_in_this_world')
	moduser = ensureUser (moduser, config)

	ensureGroupMember (modgroup, user)

	conn = await ldapclient.connect (is_async=True)

	results = await getLdapGroup (conn, config, modgroup.gr_name)
	try:
		results['memberUid'].append (moduser['name'])
		await results.modify ()
	except ValueError:
		# User already in list. This is fine.
		pass

	conn.close ()

	# flush and sanity check to make sure the user is now in the group
	ok = False
	for i in range (60):
		await flushUserCache ()

		res = grp.getgrnam (modgroup.gr_name)
		if moduser['name'] in res.gr_mem:
			logger.debug (f'User {moduser["name"]} in group {modgroup.gr_name} ({res.gr_mem}), moving on.')
			ok = True
			break
		else:
			logger.debug (f'User {moduser["name"]} not yet in group {modgroup.gr_name}, waiting.')
			await asyncio.sleep (1)
	if not ok:
		raise ServerError ({'status': 'resolve_timeout'})

	logger.info (f'Added user {moduser["name"]} to group {modgroup.gr_name}')
	return response.json ({'status': 'ok'})

async def garbageCollectGroups (config, conn):
	""" Remove groups with no members. """
	query = f'(&(objectClass=posixGroup)(gidNumber>={config.MIN_GID})(gidNumber<={config.MAX_GID})(!(memberUid=*)))'
	logger.info (f'Searching orphan groups with query {query}')
	results = await conn.search (config.LDAP_BASE_GROUP,
			bonsai.LDAPSearchScope.SUBTREE, query)
	for g in results:
		logger.info (f'Garbage-collected group {g["cn"]} with members {g.get("memberUid")}')
		await g.delete ()

@bp.route ('/group/<delgroup>', methods=['DELETE'])
@authorized('KERBEROS_USER')
async def deleteGroup (request, delgroup, user):
	"""
	Delete user from a group.
	"""

	config = request.app.config

	delgroup = ensureGroup (delgroup, config)
	user = ensureUser (user, config)

	conn = await ldapclient.connect (is_async=True)

	# make sure this is nobody’s primary group (it should not happen)
	results = await conn.search (config.LDAP_BASE_PEOPLE,
			bonsai.LDAPSearchScope.SUBTREE,
			f'(gidHumber={delgroup.gr_gid})')
	if len (results) > 0:
		raise Forbidden (dict (status='primary_group'))

	results = await getLdapGroup (conn, config, delgroup.gr_name)
	try:
		results['memberUid'].remove (user['name'])
	except (KeyError, ValueError):
		# KeyError, if entry has no members (i.e. no memberUid),
		# ValueError, if user not in members.
		raise NotFound ({'status': 'not_a_member'})
	await results.modify ()
	logger.info (f'Removed user {user["name"]} from group {delgroup.gr_name}')

	await garbageCollectGroups (config, conn)
	conn.close ()

	# flush and sanity check to make sure the user not in the group any more.
	ok = False
	for i in range (60):
		await flushUserCache ()

		# Either the group disappears (KeyError) or the membership does.
		try:
			res = grp.getgrnam (delgroup.gr_name)
			if user['name'] not in res.gr_mem:
				ok = True
				break
		except KeyError:
			ok = True
			break

		logger.info (f'User {user["name"]} still in group {delgroup.gr_name} ({res.gr_name}) and group still exists, waiting.')
		await asyncio.sleep (1)
	if not ok:
		raise ServerError ({'status': 'resolve_timeout'})

	return response.json ({'status': 'ok'})

