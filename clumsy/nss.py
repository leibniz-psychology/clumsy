import pwd

def getUser (x):
	if isinstance (x, str):
		entry = pwd.getpwnam (x)
	elif isinstance (x, int):
		entry = pwd.getpwuid (x)
	else:
		raise ValueError ('invalid input')

	return dict (name = entry.pw_name, homedir = entry.pw_dir, uid = entry.pw_uid, gid = entry.pw_gid)

