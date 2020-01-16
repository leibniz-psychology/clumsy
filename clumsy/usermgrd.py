"""
Create and delete user accounts on behalf of other daemons.

Users must be local to the system this service is running on.
"""

import secrets, bonsai, random

import aiohttp
from sanic import Sanic, Blueprint, response
from sanic.log import logger

from .nss import getUser
from .kadm import KAdm, KAdmException

client = None
kadm = None
reservedUid = set ()

def randomSecret (n):
	alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789'
	return ''.join (secrets.choice (alphabet) for i in range (n))

def socketSession (path):
	conn = aiohttp.UnixConnector (path=path)
	return aiohttp.ClientSession(connector=conn)

async def flushUserCache (sockpath):
	"""
	Flush the local user caches
	"""
	async with socketSession (sockpath) as session:
		# hostname does not matter for unix domain socket?
		async with session.delete (f'http://localhost/account') as resp:
			deldata = await resp.json ()
			if deldata['status'] != 'ok':
				return response.json ({'status': 'cache_flush'}, status=500)

bp = Blueprint('usermgrd')

@bp.listener('before_server_start')
async def setup (app, loop):
	global client, kadm

	config = app.config

	client = bonsai.LDAPClient (config.LDAP_SERVER)
	client.set_credentials ("SIMPLE", user=config.LDAP_USER, password=config.LDAP_PASSWORD)

	kadm = KAdm (config.KERBEROS_USER, config.KERBEROS_KEYTAB)

@bp.route ('/', methods=['POST'])
async def addUser (request):
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

	async with client.connect (is_async=True) as conn:
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
		await conn.add (o)

		o = bonsai.LDAPEntry (f"cn={user},ou=group,dc=compute,dc=zpid,dc=de")
		o['objectClass'] = ['top', 'posixGroup']
		o['cn'] = user
		o['gidNumber'] = gid
		o['memberUid'] = user
		await conn.add (o)
	await flushUserCache (config.NSCDFLUSHD_SOCKET)
	reservedUid.remove (uid)

	try:
		logger.debug ('adding kerberos user')
		await kadm.addPrincipal (user, password)
	except KAdmException:
		return response.json ({'status': 'kerberos_failed', 'ldap_code': ret}, status=500)

	# create homedir
	async with socketSession (config.MKHOMEDIRD_SOCKET) as session:
		async with session.post (f'http://localhost/{user}') as resp:
			data = await resp.json ()
			if data['status'] != 'ok':
				return response.json ({'status': 'mkhomedir_failed', 'mkhomedird_status': data['status']})

	# XXX: roll back, in case anything above fails

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
		return response.json ({'status': 'user_not_found'}, status=404)

	if not (config.MIN_UID <= res['uid'] < config.MAX_UID):
		return response.json ({'status': 'unauthorized'}, status=403)

	# disallow logging in by deleting principal
	try:
		await kadm.getPrincipal (user)
		# XXX: race-condition
		await kadm.deletePrincipal (user)
	except KeyError:
		logger.warn (f'kerberos principal for {user} already gone')
	except KAdmException:
		return response.json ({'status': 'kerberos_failed', 'ldap_code': ret}, status=500)

	# mark homedir for deletion
	session = socketSession (config.MKHOMEDIRD_SOCKET)
	async with session.delete (f'http://localhost/{user}') as resp:
		deldata = await resp.json ()
		if deldata['status'] != 'again':
			return response.json ({'status': 'mkhomedird_token', 'mkhomedird_status': deldata['status']})

	async with client.connect (is_async=True) as conn:
		await conn.delete (f"uid={user},ou=people,dc=compute,dc=zpid,dc=de")
		await conn.delete (f"cn={user},ou=group,dc=compute,dc=zpid,dc=de")

	await flushUserCache (config.NSCDFLUSHD_SOCKET)

	# finally delete homedir
	async with session.delete (f'http://localhost/{user}', params={'token': deldata['token']}) as resp:
		deldata = await resp.json ()
		if deldata['status'] != 'ok':
			return response.json ({'status': 'mkhomedir_delete', 'mkhomedird_status': deldata['status']})

	await session.close ()

	return response.json ({'status': 'ok'})

