"""
Handle homedir creation/deleteion

Needs permission to create new directory in home basedir and to chown them to
the proper user (CAP_CHOWN). Users must be present in local user database (via
nss).
"""

import os, shutil, asyncio, subprocess, secrets, stat
from datetime import datetime, timedelta

from sanic import Blueprint, response
from sanic.log import logger

from .nss import getUser

def randomSecret (n=32):
	alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
	return ''.join (secrets.choice (alphabet) for i in range (n))

running = set ()
deleteToken = dict ()
skeldir = '/etc/skel/'

if not skeldir.endswith ('/'):
	skeldir += '/'

bp = Blueprint('mkhomedird')

@bp.route ('/<user>', methods=['POST'])
async def touchHome (request, user):
	"""
	Create a user’s home

	User must exist and have a valid, but nonexistent homedir set
	"""

	config = request.app.config

	if user in running:
		# XXX: wait for response and return it
		return response.json ({'status': 'in_progress'}, status=202)
	running.add (user)

	try:
		try:
			userdata = getUser (user)
		except KeyError:
			return response.json ({'status': 'user_not_found'}, status=404)
		homedir = userdata['homedir']
		logger.debug (f'home is {homedir}')
		# make sure all dirs end with / (for rsync)
		if not homedir.endswith ('/'):
			homedir += '/'

		mode = 0o750
		try:
			os.mkdir (homedir, mode=mode)
		except FileExistsError:
			return response.json ({'status': 'homedir_exists'}, status=409)

		cmd = ['rsync', '-av', f'--chown={userdata["uid"]}:{userdata["gid"]}', skeldir, homedir]
		logger.debug (' '.join (cmd))
		proc = await asyncio.create_subprocess_exec (*cmd, stdin=subprocess.DEVNULL)
		ret = await proc.wait ()
		if ret != 0:
			return response.json ({'status': 'copy_skeleton_failed'}, status=500)

		# make sure the directory has proper permissions after rsync messes them up
		os.chmod (homedir, mode)

		# create shared directory
		sharedDir = os.path.join (config.SHARED_PATH, userdata['name'])
		try:
			os.mkdir (sharedDir, mode=mode)
			os.chown (sharedDir, userdata["uid"], userdata["gid"])
		except FileExistsError:
			return response.json ({'status': 'shared_dir_exists'})
	finally:
		running.remove (user)

	return response.json ({'status': 'ok'}, status=201)

def remove_readonly(func, path, _):
	"Clear the readonly bit and reattempt the removal"
	os.chmod(path, stat.S_IWRITE)
	func(path)

@bp.route ('/<user>', methods=['DELETE'])
async def deleteHome (request, user):
	"""
	Delete a user’s homedir

	Two-step verification needed: First, call without a token query string to
	obtain a token, then delete the user and call again with token.

	XXX: make sure homedir fits a certain pattern (to avoid arbitrary dir deletion)
	"""

	config = request.app.config
	token = request.args.get ('token')

	if not token:
		# get a new token
		try:
			userdata = getUser (user)
		except KeyError:
			return response.json ({'status': 'user_not_found'}, status=404)

		while True:
			newToken = randomSecret ()
			if newToken in deleteToken:
				continue
			deleteToken[newToken] = (datetime.utcnow (), userdata)
			return response.json ({'status': 'again', 'token': newToken})
	else:
		try:
			date, userdata = deleteToken[token]
			if user != userdata['name']:
				raise KeyError ('wrong user')
		except KeyError:
			return response.json ({'status': 'token_invalid'}, status=403)

		# token is not expired
		if datetime.utcnow () - date > timedelta (seconds=60):
			return response.json ({'status': 'token_expired'}, status=403)

		# make sure the user is actually gone
		try:
			currentUserdata = getUser (userdata['name'])
			return response.json ({'status': 'user_exists'}, status=403)
		except KeyError:
			pass

		sharedDir = os.path.join(config.SHARED_PATH, userdata['name'])
		for d in (userdata['homedir'], sharedDir, f'/var/guix/profiles/per-user/{user}'):
			if os.path.exists (d):
				logger.debug (f'deleting directory {d}')
				shutil.rmtree (d, onerror=remove_readonly)

		return response.json ({'status': 'ok'})

