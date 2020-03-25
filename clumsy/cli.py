import sys, socket, shutil, os, asyncio

from sanic import Sanic, Blueprint

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
		asyncio.run (modulebp ())

