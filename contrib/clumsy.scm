(define-module (clusmy)
  #:use-module ((guix licenses) #:prefix license:)
  #:use-module (gnu packages)
  #:use-module (gnu packages acl)
  #:use-module (gnu packages openldap)
  #:use-module (gnu packages python-web)
  #:use-module (gnu packages python-xyz)
  #:use-module (gnu packages check)
  #:use-module (guix packages)
  #:use-module (guix download)
  #:use-module (guix git-download)
  #:use-module (guix build-system python)
  #:use-module (guix gexp)
  #:use-module (srfi srfi-1)
  #:use-module (srfi srfi-26))

(define %source-dir (dirname (dirname (current-filename))))

(define-public python-www-authenticate
  (package
    (name "python-www-authenticate")
    (version "0.9.2")
    (source (origin
              (method git-fetch)
              (uri (git-reference
                    (url "https://github.com/alexdutton/www-authenticate.git")
                    (commit version)))
              (file-name (git-file-name name version))
              (sha256 (base32 "01p6qm4fyg3w6j8xwk1lwxb0jhmblagwnzr6vi7gk8gddq8apn8s"))))
    (build-system python-build-system)
    (native-inputs (list python-nose))
    (home-page "https://github.com/alexsdutton/www-authenticate")
    (synopsis "Parser for WWW-Authenticate headers.")
    (description "Parser for WWW-Authenticate headers.")
    (license license:bsd-3)))

(package
  (name "clumsy")
  (version "0.1")
  (source (local-file %source-dir #:recursive? #t))
  (build-system python-build-system)
  (arguments
   ;; cannot run tests, they depend on local user accounts
   `(#:tests? #f
     #:phases
     (modify-phases %standard-phases
       (add-after 'unpack 'patch-paths
         (lambda* (#:key inputs native-inputs #:allow-other-keys)
           (substitute* "clumsy/usermgrd.py"
             (("'setfacl'") (string-append "'" (assoc-ref inputs "acl") "/bin/setfacl'"))))))))
  (inputs `(("acl" ,acl)))
  (propagated-inputs
   `(("python-sanic" ,python-sanic)
     ("python-aiohttp" ,python-aiohttp)
     ("python-unidecode" ,python-unidecode)
     ("python-bonsai" ,python-bonsai)
     ("python-gssapi" ,python-gssapi)
     ("python-www-authenticate" ,python-www-authenticate)))
  (native-inputs
    `(("python-pytest" ,python-pytest)
      ("python-pytest-runner" ,python-pytest-runner)
      ("python-pytest-cov" ,python-pytest-cov)
      ("python-pytest-asyncio" ,python-pytest-asyncio)
      ("python-k5test" ,python-k5test)))
  (home-page #f)
  (synopsis #f)
  (description #f)
  (license #f))

