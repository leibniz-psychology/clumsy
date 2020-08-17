"""
Create and delete user accounts on behalf of other daemons.

Users must be local to the system this service is running on.
"""

import secrets, bonsai, random, functools, re, asyncio, os, time
from pwd import getpwuid
from contextlib import AsyncExitStack
from collections import namedtuple

import aiohttp
from sanic import Blueprint, response
from sanic.log import logger
from sanic.exceptions import ServerError, Forbidden, NotFound
from unidecode import unidecode

from .nss import getUser
from .kadm import KAdm, KAdmException

ldapc = None
kadm = None
flushsession = None
homedirsession = None
delToken = dict ()

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
		if len (u) >= minlen:
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

	o = bonsai.LDAPEntry (f"uid={user},ou=people,dc=compute,dc=zpid,dc=de")
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
	o['homeDirectory'] = f'/home/{user}'
	o['loginShell'] = '/bin/bash'
	try:
		logger.debug (f'adding user {o} to ldap')
		await ldapc.add (o)
		# LIFO -> flush cache last
		rollback.push_async_callback (flushUserCache)
		rollback.push_async_callback (o.delete)
	except bonsai.errors.AlreadyExists:
		raise ServerError ({'status': 'user_exists'})

	o = bonsai.LDAPEntry (f"cn={user},ou=group,dc=compute,dc=zpid,dc=de")
	o['objectClass'] = ['top', 'posixGroup']
	o['cn'] = user
	o['gidNumber'] = gid
	o['memberUid'] = user
	try:
		logger.debug (f'adding group {o} to ldap')
		await ldapc.add (o)
		rollback.push_async_callback (o.delete)
	except bonsai.errors.AlreadyExists:
		raise ServerError ({'status': 'group_exists'})

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
		await kadm.addPrincipal (user, password)
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

	if user not in delToken:
		# get a new token
		try:
			res = getUser (user)
			uid = res['uid']
			start = time.time()
			newToken = randomSecret(32)
			delFile = os.path.join(res['homedir'] + '/' + 'confirm_deletion' + '_' + newToken)
			delUser = res['name']
			delToken[delUser] = (delFile, start)
			if os.path.isfile(delFile) == False:
				return response.json ({'status': 'delete', 'token': delFile})
		except KeyError:
			raise NotFound ({'status': 'user_not_found'})
	else:
		try:
			res = getUser (user)
			uid = res['uid']
			delFile, start = delToken.pop (user)
			delUser = res['name']
		except KeyError:
			raise NotFound ({'status': 'no_token'})

	if not (config.MIN_UID <= uid < config.MAX_UID):
		raise Forbidden ({'status': 'unauthorized'})

	# check whether the file exists, belongs to the user who requested deletion, both the request and the token is recent
	try:
		owner = getpwuid(os.stat(delFile).st_uid).pw_name
	except FileNotFoundError:
		raise NotFound ({'status': 'no_proof'})

	if owner == delUser and (time.time() - start) <= 60 and (time.time() - os.path.getctime(delFile)) <= 60:
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
	else:
		# user did not prove he is allowed to do this
		raise Forbidden ({'status': 'invalid_proof'})

