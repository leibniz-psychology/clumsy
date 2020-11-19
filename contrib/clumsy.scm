(define-module (clusmy)
  #:use-module ((guix licenses) #:prefix license:)
  #:use-module (gnu packages)
  #:use-module (gnu packages acl)
  #:use-module (gnu packages openldap)
  #:use-module (gnu packages python-web)
  #:use-module (gnu packages python-xyz)
  #:use-module (gnu packages check)
  #:use-module (zpid packages sanic)
  #:use-module (guix packages)
  #:use-module (guix download)
  #:use-module (guix build-system python)
  #:use-module (guix gexp)
  #:use-module (srfi srfi-1)
  #:use-module (srfi srfi-26))

(define %source-dir (dirname (dirname (current-filename))))

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
     ("python-bonsai" ,python-bonsai)))
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

