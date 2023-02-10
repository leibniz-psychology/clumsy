# Copyright 2019–2023 Leibniz Institute for Psychology
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
from contextlib import AsyncExitStack, contextmanager
from collections import namedtuple

import aiohttp, bonsai, structlog
from sanic import Blueprint, response
from sanic.exceptions import ServerError, Forbidden, NotFound
from unidecode import unidecode

from .nss import getUser
from .kadm import KAdm, KAdmException
from .gssapi.server import authorized
from .uid import uintToQuint

ldapclient = None
kadm = None
flushsession = None
homedirsession = None

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
	logger = structlog.get_logger ()
	try:
		async with flushsession.delete ('http://localhost/account') as resp:
			deldata = await resp.json ()
			if deldata['status'] != 'ok':
				logger.error ('flush_cache_failed', response=deldata)
				raise ServerError ({'status': 'flush_failed', 'nscdflush_status': deldata['status']})
	except aiohttp.ClientError as e:
		logger.error ('flush_cache_unavailable', exception=e)
		raise ServerError ({'status': 'nscdflushd_connect'})

def keepAscii (s):
	""" Drop all non-ASCII characters (probably more) from s """
	return re.sub (r'[^0-9a-zA-Z @+-]+', '', s)

# revoke ACL while deleting the user
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

reservedUsers = set ()

@contextmanager
def findUnusedUser (it):
	""" From iterator it find unused user or user id """
	ret = None
	for u in it:
		if u in reservedUsers:
			continue
		try:
			res = getUser (u)
		except KeyError:
			ret = u
			break
	reservedUsers.add (ret)
	try:
		yield ret
	finally:
		reservedUsers.remove (ret)

@bp.route ('/user', methods=['POST'])
# @authorize is not async, but I’m not aware of any async gssapi module -.-
@authorized('KERBEROS_USER')
@withRollback
async def addUser (request, rollback, user):
	config = request.app.config

	if user.split ('@', 1)[0] != config.AUTHORIZATION_CREATE:
		raise Forbidden ()

	form = request.json
	logger = structlog.get_logger ()
	logger = logger.bind (user=user, data=form)
	logger.info ('add_user_start')
	userdata = UserInfo (**form)

	with findUnusedUser ([random.randrange (config.MIN_UID, config.MAX_UID) \
			for i in range (100)]) as uid:
		gid = uid
		if not uid:
			logger.error ('add_user_no_uid', uid=uid)
			raise ServerError ({'status': 'uid'})
		user = f'user-{uintToQuint (uid, 2)}'

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
		o['mail'] = keepAscii (userdata.email)
		# LDAP: posixAccount
		o['uid'] = user
		o['uidNumber'] = uid
		o['gidNumber'] = gid
		o['homeDirectory'] = config.HOME_TEMPLATE.format (user=user)
		o['loginShell'] = '/bin/bash'
		o['gecos'] = keepAscii (userdata.username)
		o['description'] = userdata.authorization
		try:
			logger.info ('add_user_ldap', ldapUser=o)
			await conn.add (o)
			# LIFO -> flush cache last
			rollback.push_async_callback (flushUserCache)
			rollback.push_async_callback (o.delete)
		except bonsai.errors.AlreadyExists:
			logger.info ('add_user_ldap_exists', ldapUser=o)
			raise ServerError ({'status': 'user_exists'})

		o = bonsai.LDAPEntry (f'cn={user},{config.LDAP_BASE_GROUP}')
		o['objectClass'] = ['top', 'posixGroup']
		o['cn'] = user
		o['gidNumber'] = gid
		o['memberUid'] = user
		try:
			logger.info ('add_user_ldap_group', ldapGroup=o)
			await conn.add (o)
			rollback.push_async_callback (o.delete)
		except bonsai.errors.AlreadyExists:
			logger.info ('add_user_ldap_group_exists', ldapGroup=o)
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
				logger.error ('add_user_mismatch', fromName=resUser, fromUid=resUid)
				raise ServerError ({'status': 'user_mismatch'})
			ok = True
			break
		except KeyError:
			logger.debug ('add_user_flush_retry')
			await asyncio.sleep (1)
	if not ok:
		logger.error ('add_user_flush_failed')
		raise ServerError ({'status': 'user_add_failed'})

	try:
		logger.info ('add_user_kerberos', user=user, expire=config.KERBEROS_EXPIRE)
		password = randomSecret (32)
		await kadm.addPrincipal (user, password, expire=config.KERBEROS_EXPIRE)
		rollback.push_async_callback (kadm.deletePrincipal, user)
	except KAdmException as e:
		logger.error ('add_user_kerberos_failed', exc_info=e)
		raise ServerError ({'status': 'kerberos_failed'})

	# create homedir
	try:
		async with homedirsession.post (f'http://localhost/user/{user}') as resp:
			data = await resp.json ()
			if data['status'] != 'ok':
				logger.error ('add_user_mkhomedir_failed', response=data)
				raise ServerError ({'status': 'mkhomedir_failed', 'mkhomedird_status': data['status']})
	except aiohttp.ClientError as e:
		logger.error ('add_user_mkhomedir_unavailable', exc_info=e)
		raise ServerError ({'status': 'mkhomedird_connect'})

	logger.info ('add_user_success')
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
	logger = structlog.get_logger ()
	logger = logger.bind (user=user)
	logger.info ('delete_user_start')

	try:
		user = user.split ('@', 1)[0]
		res = getUser (user)
		uid = res['uid']
		gid = res['gid']
		logger = logger.bind (userinfo=res)
		logger.info ('delete_user_lookup')
	except KeyError:
		logger.error ('delete_user_not_found')
		raise NotFound ({'status': 'user_not_found'})

	if not (config.MIN_UID <= uid < config.MAX_UID):
		logger.error ('delete_user_uid_requirement_not_met')
		raise Forbidden ({'status': 'unauthorized'})

	# disallow logging in by deleting principal
	try:
		await kadm.getPrincipal (user)
		# XXX: race-condition
		await kadm.deletePrincipal (user)
	except KeyError:
		logger.warning ('delete_user_kerberos_gone')
	except KAdmException as e:
		logger.error ('delete_user_kerberos_failed', exc_info=e)
		raise ServerError ({'status': 'kerberos_failed'})

	# mark homedir for deletion
	try:
		async with homedirsession.delete (f'http://localhost/user/{user}') as resp:
			deldata = await resp.json ()
			if deldata['status'] != 'again':
				logger.error ('delete_user_mkhomedir_failed_token', result=deldata)
				raise ServerError ({'status': 'mkhomedird_token', 'mkhomedird_status': deldata['status']})
	except aiohttp.ClientError as e:
		logger.error ('delete_user_mkhomedir_unavailable_token', exc_info=e)
		raise ServerError ({'status': 'mkhomedird_connect_token'})

	conn = await ldapclient.connect (is_async=True)
	try:
		await conn.delete (f'uid={user},{config.LDAP_BASE_PEOPLE}')
	except bonsai.errors.NoSuchObjectError:
		logger.warning ('delete_user_ldap_gone')
	try:
		await conn.delete (f'cn={user},{config.LDAP_BASE_GROUP}')
	except bonsai.errors.NoSuchObjectError:
		logger.warning ('delete_user_ldap_group_gone')
	# Find all secondary groups user is member in and delete membership.
	results = await conn.search (config.LDAP_BASE_GROUP,
			bonsai.LDAPSearchScope.SUBTREE,
			f'(&(objectClass=posixGroup)(memberUid={user}))')
	for g in results:
		logger.info (f'delete_user_group_membership', group=g)
		g['memberUid'].remove (user)
		await g.modify ()
	await garbageCollectGroups (config, conn)
	conn.close ()

	await flushUserCache ()

	# finally delete homedir
	try:
		async with homedirsession.delete (f'http://localhost/user/{user}', params={'token': deldata['token']}) as resp:
			deldata = await resp.json ()
			if deldata['status'] != 'ok':
				logger.error ('delete_user_mkhomedir_failed_delete', result=deldata)
				raise ServerError ({'status': 'mkhomedir_delete', 'mkhomedird_status': deldata['status']})
	except aiohttp.ClientError as e:
		logger.error ('delete_user_mkhomedir_unavailable_delete', exc_info=e)
		raise ServerError ({'status': 'mkhomedird_connect_delete'})

	logger.info ('delete_user_success')
	return response.json ({'status': 'ok'})

reservedGroups = set ()

@contextmanager
def findUnusedGroup (it):
	""" From iterator it find unused group or group id """
	g = None
	for u in it:
		try:
			if u in reservedGroups:
				continue
			if isinstance (u, str):
				res = grp.getgrnam (u)
			elif isinstance (u, int):
				res = grp.getgrgid (u)
		except KeyError:
			g = u
			break
	reservedGroups.add (g)
	try:
		yield g
	finally:
		reservedGroups.remove (g)

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
	logger = structlog.get_logger ()
	logger = logger.bind (user=user, groupName=newGroupName)
	logger.info ('add_group_start')

	owner = ensureUser (user, config)

	with findUnusedGroup ([random.randrange (config.MIN_GID, config.MAX_GID) \
			for i in range (100)]) as gid:
		if not gid:
			raise ServerError ({'status': 'gid'})
		group = f'group-{uintToQuint(gid, 2)}'

		conn = await ldapclient.connect (is_async=True)
		o = bonsai.LDAPEntry(f'cn={group},{config.LDAP_BASE_GROUP}')
		o['objectClass'] = [
				'top',
				'posixGroup',
				]
		o['cn'] = group
		o['gidNumber'] = gid
		o['memberUid'] = owner['name']
		o['description'] = f'Created by {user} for {newGroupName}'
		try:
			logger.info ('add_group_ldap', ldapGroup=o)
			await conn.add (o)
			# LIFO -> flush cache last
			rollback.push_async_callback (flushUserCache)
			rollback.push_async_callback (o.delete)
		except bonsai.errors.AlreadyExists:
			logger.error ('add_group_ldap_exists')
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
				logger.error ('add_group_mismatch', fromName=resNam, fromUid=resGid)
				raise ServerError ({'status': 'group_mismatch'})
			ok = True
			break
		except KeyError:
			logger.debug ('add_group_flush_retry')
			await asyncio.sleep (1)
	if not ok:
		logger.error ('add_group_flush_failed')
		raise ServerError ({'status': 'resolve_timeout'})

	logger.info ('add_group_success')
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
	logger = structlog.get_logger ()
	logger = logger.bind (user=user, group=modgroup, addUser=moduser)
	logger.info ('add_user_to_group_start')

	modgroup = ensureGroup (modgroup, config)
	gid = modgroup.gr_gid

	user = ensureUser (user, config, 'you_do_not_exist_in_this_world')
	moduser = ensureUser (moduser, config)

	ensureGroupMember (modgroup, user)

	logger = logger.bind (addUser=moduser, group=modgroup)

	conn = await ldapclient.connect (is_async=True)

	results = await getLdapGroup (conn, config, modgroup.gr_name)
	try:
		logger.info ('add_user_to_group_ldap', ldapGroup=results)
		results['memberUid'].append (moduser['name'])
		await results.modify ()
	except ValueError:
		# User already in list. This is fine.
		logger.warning ('add_user_to_group_ldap_exists', ldapGroup=results)

	conn.close ()

	# flush and sanity check to make sure the user is now in the group
	ok = False
	for i in range (60):
		await flushUserCache ()

		res = grp.getgrnam (modgroup.gr_name)
		if moduser['name'] in res.gr_mem:
			ok = True
			break
		else:
			logger.debug ('add_user_to_group_flush_retry')
			await asyncio.sleep (1)
	if not ok:
		logger.error ('add_user_to_group_flush_failed')
		raise ServerError ({'status': 'resolve_timeout'})

	logger.info ('add_user_to_group_success')
	return response.json ({'status': 'ok'})

async def garbageCollectGroups (config, conn):
	""" Remove groups with no members. """
	query = f'(&(objectClass=posixGroup)(gidNumber>={config.MIN_GID})(gidNumber<={config.MAX_GID})(!(memberUid=*)))'
	logger = structlog.get_logger ()
	logger.info ('gc_groups_start')
	gids = []
	results = await conn.search (config.LDAP_BASE_GROUP,
			bonsai.LDAPSearchScope.SUBTREE, query)
	for g in results:
		logger.info ('gc_groups_ldap_delete', group=g)
		try:
			await g.delete ()
			gids.append (str (g['gidNumber'][0]))
		except bonsai.errors.NoSuchObjectError:
			# Someone else removed it. That’s fine.
			pass

	if gids:
		gids = ','.join (gids)
		async with homedirsession.delete (f'http://localhost/group/{gids}') as resp:
			deldata = await resp.json ()
			if deldata['status'] != 'ok':
				logger.error ('gc_groups_mkhomedir_failed', result=deldata, gids=gids)
				raise ServerError ({'status': 'mkhomedir_group_delete', 'mkhomedird_status': deldata['status']})

@bp.route ('/group/<delgroup>', methods=['DELETE'])
@authorized('KERBEROS_USER')
async def deleteGroup (request, delgroup, user):
	"""
	Delete user from a group.
	"""

	config = request.app.config
	logger = structlog.get_logger ()
	logger = logger.bind (group=delgroup, user=user)
	logger.info ('delete_group_start')

	delgroup = ensureGroup (delgroup, config)
	user = ensureUser (user, config)
	logger = logger.bind (group=delgroup, user=user)

	conn = await ldapclient.connect (is_async=True)

	# make sure this is nobody’s primary group (it should not happen)
	results = await conn.search (config.LDAP_BASE_PEOPLE,
			bonsai.LDAPSearchScope.SUBTREE,
			f'(gidHumber={delgroup.gr_gid})')
	if len (results) > 0:
		logger.error ('delete_group_is_primary', results=results)
		raise Forbidden (dict (status='primary_group'))

	results = await getLdapGroup (conn, config, delgroup.gr_name)
	try:
		logger.info ('delete_group_ldap', ldapGroup=results)
		results['memberUid'].remove (user['name'])
	except (KeyError, ValueError):
		# KeyError, if entry has no members (i.e. no memberUid),
		# ValueError, if user not in members.
		logger.error ('delete_group_ldap_not_a_member')
		raise NotFound ({'status': 'not_a_member'})
	await results.modify ()

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

		logger.debug ('delete_group_flush_retry')
		await asyncio.sleep (1)
	if not ok:
		logger.error ('delete_group_flush_timeout')
		raise ServerError ({'status': 'resolve_timeout'})

	logger.info ('delete_group_success')
	return response.json ({'status': 'ok'})

