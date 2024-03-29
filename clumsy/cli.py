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

import sys, socket, shutil, os, asyncio, logging

from sanic import Sanic, Blueprint
from sanic.exceptions import SanicException
from sanic.response import json
import structlog

from .mkhomedird import bp as mkhomedird
from .nscdflushd import bp as nscdflushd
from .usermgrd import bp as usermgrd
from .ktwkd import ktwkd

logger = structlog.get_logger ()

class StructLogHandler (logging.Handler):
	""" Forward messages from Python’s own logging module to structlog """
	def emit (self, record):
		lvl = record.levelname.lower ()
		f = getattr (logger, lvl)
		f ('logging.' + record.name, message=record.getMessage (), exc_info=record.exc_info)

def main ():
	name = sys.argv[1]
	modulebp = {'mkhomedird': mkhomedird, 'nscdflushd': nscdflushd, 'usermgrd': usermgrd, 'ktwkd': ktwkd}[name]

	if isinstance (modulebp, Blueprint):
		structlog.configure (
			wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
			processors=[
				structlog.threadlocal.merge_threadlocal_context,
				structlog.processors.add_log_level,
				structlog.processors.format_exc_info,
				structlog.processors.TimeStamper(fmt="iso", utc=False),
				structlog.processors.JSONRenderer(),
			],
			logger_factory=structlog.PrintLoggerFactory(),
		)

		# Forward Python logging to structlog
		rootLogger = logging.getLogger ()
		structHandler = StructLogHandler ()
		rootLogger.addHandler (structHandler)
		rootLogger.setLevel (logging.INFO)

		app = Sanic (name, configure_logging=False)
		app.config.update_config (os.environ['SETTINGS_FILE'])
		config = app.config
		app.blueprint (modulebp)

		@app.exception(Exception)
		async def handleErrors (request, exc):
			if isinstance (exc, SanicException):
				logger.error (exc.args[0], exc_info=exc)
				return json (exc.args[0], status=exc.status_code)
			else:
				logger.error ('bug', exc_info=exc)
				return json ({'status': 'bug'}, status=500)

		args = {'access_log': False}
		try:
			if config.DEBUG:
				args['debug'] = True
				args['auto_reload'] = True
		except AttributeError:
			# no debugging then
			pass

		args['sock'] = sock = socket.socket (socket.AF_UNIX)
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

		app.run (**args)
	else:
		asyncio.run (modulebp ())

