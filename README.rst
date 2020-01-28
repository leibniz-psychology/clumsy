clumsy
======

The cluster management system (clumsy) provides simple user-management for
compute clusters built on top of LDAP and Kerberos. It is split into multiple
small daemons (microservices), each exposing a HTTP JSON API.

mkhomedird
^^^^^^^^^^

Create and populate or delete user’s home and spool directories on demand. When
kerberizing NFS shares pam_mkhomedir_ is not usable, because the root user has
no special powers.  Instead mkhomedird can be run on the NFS server with
appropriate permissions and create the home directory on request.

nscdflushd
^^^^^^^^^^

Flushe sssd’s and nscd’s caches. Both, sssd and nscd cache user and group
information. When updating a remote LDAP-powered user database these need to be
flushed to allow nss pick up the changes.

usermgrd
^^^^^^^^

Create and delete users. Modifies users and groups in LDAP, principals in
Kerberos and calls out to mkhomedird and nscdflushd.

Creating a user is as simple as

.. code:: console

	$ http POST http://example.com/
	HTTP/1.1 201 Created

	{
		"gid": 3935374,
		"password": "qrlef14b2dkf5ykrx40a4hcnzpzwe8ta",
		"status": "ok",
		"uid": 3935374,
		"user": "pseetbfgv8lbt00w4"
	}

And deleting a user works the same:

.. code:: console

	$ http DELETE http://example.com/pseetbfgv8lbt00w4
	HTTP/1.1 200 OK

	{ "status": "ok" }

.. _pam_mkhomedir: https://linux.die.net/man/8/pam_mkhomedir

Development
^^^^^^^^^^^

Unit-tests can be run with

.. code:: console

	$ python setup.py test

which also generates a coverage report in the directory ``htmlcov`` by default.
No automated integration tests exist right now.

