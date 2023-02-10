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
Handle homedir creation/deleteion

Needs permission to create new directory in home basedir and to chown them to
the proper user (CAP_CHOWN). Users must be present in local user database (via
nss).
"""

import os, shutil, asyncio, subprocess, secrets, stat
from datetime import datetime, timedelta

import structlog
from sanic import Blueprint, response

from .nss import getUser

def randomSecret (n=32):
	alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
	return ''.join (secrets.choice (alphabet) for i in range (n))

running = set ()
deleteToken = dict ()

bp = Blueprint('mkhomedird')

async def copyDir (a, b, uid, gid):
	"""
	Copy directory a to b using rsync
	"""
	def addSlash (x):
		return x if x.endswith ('/') else x+'/'

	logger = structlog.get_logger ()

	# make sure dirs end with / for rsync
	a = addSlash (a)
	b = addSlash (b)
	cmd = ['rsync', '-av', f'--chown={uid}:{gid}', a, b]
	logger.info ('copy_dir', command=cmd)
	proc = await asyncio.create_subprocess_exec (*cmd, stdin=subprocess.DEVNULL)
	ret = await proc.wait ()
	logger.info ('copy_dir_finished', result=ret)
	return ret == 0

@bp.route ('/user/<user>', methods=['POST'])
async def touchHome (request, user):
	"""
	Create a user’s home

	User must exist and have a valid, but nonexistent homedir set
	"""

	config = request.app.config
	logger = structlog.get_logger ()
	logger = logger.bind (user=user)
	logger.info ('create_home_start')

	if user in running:
		# XXX: wait for response and return it
		logger.error ('create_home_in_progress', running=running)
		return response.json ({'status': 'in_progress'}, status=202)
	running.add (user)

	try:
		try:
			userdata = getUser (user)
			logger = logger.bind (user=user)
		except KeyError:
			logger.error ('create_home_user_not_found')
			return response.json ({'status': 'user_not_found'}, status=404)

		mode = 0o750
		for d, settings in config.DIRECTORIES.items ():
			d = d.format (**userdata)
			create = settings.get ('create', False)
			if not create:
				continue

			logger.info ('create_home_mkdir', directory=d, settings=settings, mode=mode)
			try:
				os.mkdir (d, mode=mode)
				os.chown (d, userdata["uid"], userdata["gid"])
			except FileExistsError:
				logger.error ('create_home_mkdir_exists')
				return response.json ({'status': 'homedir_exists'}, status=409)

			if isinstance (create, str):
				if not await copyDir (create, d, userdata['uid'], userdata['gid']):
					logger.error ('create_home_copy_skel_failed')
					return response.json ({'status': 'copy_skeleton_failed'}, status=500)
				# make sure the directory has proper permissions after rsync messes them up
				os.chmod (d, mode)
	finally:
		running.remove (user)

	logger.info ('create_home_success')
	return response.json ({'status': 'ok'}, status=201)

def remove_readonly(func, path, _):
	"Clear the readonly bit and reattempt the removal"
	os.chmod(path, stat.S_IWRITE)
	func(path)

async def revokeAcl (dirs, uids=None, gids=None):
	assert uids or gids
	logger = structlog.get_logger ()

	args = ['setfacl', '-R']
	if uids:
		for u in uids:
			args.extend ([
				'-x', f'u:{u}',
				'-x', f'd:u:{u}',
				])
	if gids:
		for g in gids:
			args.extend ([
				'-x', f'g:{g}',
				'-x', f'd:g:{g}',
				])
	args.append ('--')
	args.extend (dirs)

	logger = logger.bind (directories=dirs, uids=uids, gids=gids, command=args)
	logger.info ('revoke_acl')
	proc = await asyncio.create_subprocess_exec (*args,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE)
	stdout, stderr = await proc.communicate()
	logger.info ('revoke_acl_finished', stdout=stdout, stderr=stderr)

@bp.route ('/user/<user>', methods=['DELETE'])
async def deleteHome (request, user):
	"""
	Delete a user’s homedir

	Two-step verification needed: First, call without a token query string to
	obtain a token, then delete the user and call again with token.

	XXX: make sure homedir fits a certain pattern (to avoid arbitrary dir deletion)
	"""

	config = request.app.config
	token = request.args.get ('token')
	logger = structlog.get_logger ()
	logger = logger.bind (user=user, token=token)
	logger.info ('delete_home_start')

	if not token:
		# get a new token
		try:
			userdata = getUser (user)
			logger = logger.bind (user=userdata)
		except KeyError:
			logger.error ('delete_home_user_not_found')
			return response.json ({'status': 'user_not_found'}, status=404)

		while True:
			newToken = randomSecret ()
			if newToken in deleteToken:
				continue
			deleteToken[newToken] = (datetime.utcnow (), userdata)
			logger.info ('delete_home_again', token=newToken)
			return response.json ({'status': 'again', 'token': newToken})
	else:
		try:
			date, userdata = deleteToken[token]
			logger = logger.bind (tokenCreate=date, user=userdata)
			if user != userdata['name']:
				raise KeyError ('wrong user')
		except KeyError:
			logger.error ('delete_home_token_invalid')
			return response.json ({'status': 'token_invalid'}, status=403)

		# token is not expired
		if datetime.utcnow () - date > timedelta (seconds=60):
			logger.error ('delete_home_token_expired')
			return response.json ({'status': 'token_expired'}, status=403)

		# make sure the user is actually gone
		try:
			currentUserdata = getUser (userdata['name'])
			logger.error ('delete_home_user_exists', currentUserdata=currentUserdata)
			return response.json ({'status': 'user_exists'}, status=403)
		except KeyError:
			pass

		for d, props in config.DIRECTORIES.items ():
			d = d.format (**userdata)
			if props.get('delete', False) and os.path.exists (d):
				logger.error ('delete_home_rmdir', directory=d, props=props)
				shutil.rmtree (d, onerror=remove_readonly)
		# The actual directory will be gone, but we can revoke
		# one level up.
		dirs = list (map (lambda x: x[0], filter (lambda x: x[1].get('deleteGroup', False), config.DIRECTORIES.items ())))
		await revokeAcl (dirs, uids=[userdata['uid']], gids=[userdata['gid']])

		logger.info ('delete_home_success')
		return response.json ({'status': 'ok'})

@bp.route ('/group/<gids>', methods=['DELETE'])
async def deleteGroup (request, gids):
	config = request.app.config

	logger = structlog.get_logger ()
	logger = logger.bind (gids=gids)
	logger.info ('delete_group_start')

	try:
		gids = [int (g) for g in gids.split (',')]
		logger.bind (gids=gids)
	except ValueError:
		logger.error ('delete_group_invalid_gids')
		return response.json ({'status': 'invalid_gid'}, status=400)

	# Make sure none of the groups actually exists.
#	for g in groups:
#		try:
#			res = grp.getgrgid (g)
#			return response.json ({'status': 'group_exists', 'gid': g, 'group': res.gr_name}, status=403)
#		except KeyError:
#			pass

	dirs = list (map (lambda x: x[0], filter (lambda x: x[1].get('deleteGroup', False), config.DIRECTORIES.items ())))
	await revokeAcl (dirs, gids=gids)

	logger.info ('delete_group_success')
	return response.json ({'status': 'ok'})

