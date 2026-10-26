The seal needs a PKCS#12 file (`.p12` or `.pfx`) holding a private key and its
certificate. It is read from the first source that provides one:

1. The server configuration file (`odoo.conf`, section `[options]`):

   ```ini
   sign_oca_pades_pkcs12_path = /path/to/seal.p12
   sign_oca_pades_pkcs12_password = secret
   sign_oca_pades_tsa_url = https://tsa.example.com
   ```

   The file stays on the server and never enters the database.

2. The company: go to _Sign > Settings > General settings_, section _Document Sealing
   (PAdES)_ (visible to administrators only), and upload the PKCS#12 file with its
   password. An optional _Timestamp Authority_ URL can be set there too.

A timestamp authority URL from the configuration file takes precedence over the one of
the company.

The module depends on the Python library `pyhanko`. Install `pyhanko<0.26`: pyHanko 0.26
and later require `lxml>=5.4`, while Odoo 18 environments commonly pin an older `lxml`.
Version 0.25.3 is the one this module is tested with.

The local times of the certificate page use the time zone of the request's tier
validation when `sign_oca_tier_validation` sets one, else the time zone of the company's
contact, else UTC.
