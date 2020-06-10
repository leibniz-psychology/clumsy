import sys, socket, shutil, os, asyncio, logging
from traceback import format_exc

from sanic import Sanic, Blueprint
from sanic.exceptions import SanicException
from sanic.response import json
from sanic.log import logger

from .mkhomedird import bp as mkhomedird
from .nscdflushd import bp as nscdflushd
from .usermgrd import bp as usermgrd
from .ktwkd import ktwkd

def main ():
	name = sys.argv[1]
	modulebp = {'mkhomedird': mkhomedird, 'nscdflushd': nscdflushd, 'usermgrd': usermgrd, 'ktwkd': ktwkd}[name]

	if isinstance (modulebp, Blueprint):
		app = Sanic (name)
		app.config.from_envvar (f'SETTINGS_FILE')
		config = app.config
		app.blueprint (modulebp)

		@app.exception(Exception)
		async def handleErrors (request, exc):
			if isinstance (exc, SanicException):
				logger.error (exc.args[0])
				return json (exc.args[0], status=exc.status_code)
			else:
				logger.error (format_exc ())
				return json ({'status': 'bug'}, status=500)

		sock = socket.socket (socket.AF_UNIX)
		if os.path.exists (config.SOCKET):
			os.unlink (config.SOCKET)
		sock.bind (config.SOCKET)
		try:
			shutil.chown (config.SOCKET, config.SOCKET_USER, config.SOCKET_GROUP)
		except AttributeError:
			# no config given
			pass
		os.chmod (config.SOCKET, config.SOCKET_MODE)

		# XXX: systemd?
		#lockdown_account ('nobody', 'nobody', ['chown'])

		app.run (sock=sock)
	else:
		logging.basicConfig (level=logging.INFO)
		asyncio.run (modulebp ())

