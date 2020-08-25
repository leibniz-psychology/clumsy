import pwd

name = None
homedir = None
uid = 0
gid = 0

def getUser (x):
	if isinstance (x, str):
		entry = pwd.getpwnam (x)
	elif isinstance (x, int):
		entry = pwd.getpwuid (x)
	else:
		raise ValueError ('invalid input')

	try:
		return dict (name = entry.pw_name, homedir = entry.pw_dir, uid = entry.pw_uid, gid = entry.pw_gid)
	except ValueError:
		raise KeyError ('user not found') from None

