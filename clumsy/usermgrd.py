"""
Create and delete user accounts on behalf of other daemons.

Users must be local to the system this service is running on.
"""

import secrets, bonsai, random, functools
from contextlib import AsyncExitStack

import aiohttp
from sanic import Blueprint, response
from sanic.log import logger
from sanic.exceptions import ServerError, Forbidden, NotFound, SanicException

from .nss import getUser
from .kadm import KAdm, KAdmException

ldapc = None
kadm = None
flushsession = None
homedirsession = None
reservedUid = set ()

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
	async with flushsession.delete (f'http://localhost/account') as resp:
		deldata = await resp.json ()
		if deldata['status'] != 'ok':
			raise ServerError ({'status': 'flush_failed', 'nscdflush_status': deldata['status']})

bp = Blueprint('usermgrd')

@bp.listener('before_server_start')
async def setup (app, loop):
	global ldapc, kadm, flushsession, homedirsession

	config = app.config

	client = bonsai.LDAPClient (config.LDAP_SERVER)
	client.set_credentials ("SIMPLE", user=config.LDAP_USER, password=config.LDAP_PASSWORD)
	ldapc = await client.connect (is_async=True)

	kadm = KAdm (config.KERBEROS_USER, config.KERBEROS_KEYTAB)

	flushsession = socketSession (config.NSCDFLUSHD_SOCKET)
	homedirsession = socketSession (config.MKHOMEDIRD_SOCKET)

@bp.listener('after_server_stop')
async def teardown (app, loop):
	await flushsession.close ()
	await homedirsession.close ()
	ldapc.close ()

@bp.exception(SanicException)
async def handleErrors (request, exc):
	return response.json (exc.args[0], status=exc.status_code)

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

@bp.route ('/', methods=['POST'])
@withRollback
async def addUser (request, rollback):
	config = request.app.config

	while True:
		user = 'p' + randomSecret (16)
		password = randomSecret (32)
		uid = random.randrange (config.MIN_UID, config.MAX_UID)
		gid = uid

		try:
			res = getUser (uid)
		except KeyError:
			# is this one actually free to use? (prevent race condition for two
			# clients asking for a new account at the same time)
			if uid not in reservedUid:
				reservedUid.add (uid)
				break

	o = bonsai.LDAPEntry (f"uid={user},ou=people,dc=compute,dc=zpid,dc=de")
	o['objectClass'] = [
			'top',
			'person',
			'organizationalPerson',
			'inetOrgPerson',
			'posixAccount',
			'shadowAccount',
			]
	o['sn'] = f'Project account {user}'
	o['cn'] = f'{user}'
	o['loginShell'] = '/bin/bash'
	o['uidNumber'] = uid
	o['gidNumber'] = gid
	o['uid'] = user
	o['homeDirectory'] = f'/home/{user}'
	await ldapc.add (o)
	rollback.push_async_callback (o.delete)

	o = bonsai.LDAPEntry (f"cn={user},ou=group,dc=compute,dc=zpid,dc=de")
	o['objectClass'] = ['top', 'posixGroup']
	o['cn'] = user
	o['gidNumber'] = gid
	o['memberUid'] = user
	await ldapc.add (o)
	rollback.push_async_callback (o.delete)

	await flushUserCache ()
	reservedUid.remove (uid)

	try:
		logger.debug ('adding kerberos user')
		await kadm.addPrincipal (user, password)
		rollback.push_async_callback (kadm.deletePrincipal, user)
	except KAdmException:
		raise ServerError ({'status': 'kerberos_failed'})

	# create homedir
	async with homedirsession.post (f'http://localhost/{user}') as resp:
		data = await resp.json ()
		if data['status'] != 'ok':
			raise ServerError ({'status': 'mkhomedir_failed', 'mkhomedird_status': data['status']})

	return response.json ({'status': 'ok', 'user': user, 'password': password, 'uid': uid, 'gid': uid}, status=201)

@bp.route ('/<user>', methods=['DELETE'])
async def deleteUser (request, user):
	"""
	Delete user from the cluster.

	Including: LDAP user and group, kerberos principal, home directory, guix
	profile directory
	"""

	config = request.app.config

	try:
		res = getUser (user)
	except KeyError:
		raise NotFound ({'status': 'user_not_found'})

	if not (config.MIN_UID <= res['uid'] < config.MAX_UID):
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

	try:
		await ldapc.delete (f"uid={user},ou=people,dc=compute,dc=zpid,dc=de")
		await ldapc.delete (f"cn={user},ou=group,dc=compute,dc=zpid,dc=de")
	except LDAPError as e:
		raise ServerError ({'status': 'ldap', 'ldap_status': str (e), 'ldap_code': e.code})

	await flushUserCache ()

	# finally delete homedir
	async with homedirsession.delete (f'http://localhost/{user}', params={'token': deldata['token']}) as resp:
		deldata = await resp.json ()
		if deldata['status'] != 'ok':
			raise ServerError ({'status': 'mkhomedir_delete', 'mkhomedird_status': deldata['status']})

	return response.json ({'status': 'ok'})

